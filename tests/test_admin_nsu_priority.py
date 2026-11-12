"""Prioridades consNSU: fila administrativa e integração com recuperação."""
from types import SimpleNamespace

import pytest

from nfe_consulta.banco import BancoManifestacoes
from nfe_consulta.modelos import RetornoDistribuicao
from nfe_consulta.recuperacao_nsu import recuperar_intervalos
from nfe_consulta.sincronizacao import sincronizar
from nfe_consulta.web.nsu_priority import enfileirar_nsu, cancelar_nsu, listar_fila


CNPJ = "16840128000101"
CHAVE = "35260123456789000123550010000012341000012345"


def setup(tmp_path, *, lacuna=False):
    caminho = tmp_path / "atlas.db"
    banco = BancoManifestacoes(str(caminho))
    banco.salvar_retorno(
        CNPJ, RetornoDistribuicao(138, "", "000000000000020", "000000000000020", ())
    )
    if lacuna:
        with banco.conexao:
            banco.conexao.execute(
                "INSERT INTO recuperacoes_nsu(cnpj, nsu_local, nsu_sefaz) VALUES (?,?,?)",
                (CNPJ, "000000000000010", "000000000000012"),
            )
    settings = SimpleNamespace(
        database_path=caminho,
        database_path_file=tmp_path / "nao-existe.txt",
        current_database_password=lambda: None,
    )
    return banco, settings


def retorno(nsu, status=138):
    import base64
    import gzip

    event = f'<resEvento><chNFe>{CHAVE}</chNFe><tpEvento>210210</tpEvento><dhEvento>2026-10-09T10:00:00-03:00</dhEvento></resEvento>'
    doc = base64.b64encode(gzip.compress(event.encode())).decode()
    body = f'<docZip NSU="{nsu}" schema="resEvento">{doc}</docZip>' if status == 138 else ''
    return f'<retDistDFeInt><cStat>{status}</cStat><ultNSU>99</ultNSU><maxNSU>99</maxNSU>{body}</retDistDFeInt>'


def test_enfileirar_validar_e_cancelar(tmp_path):
    banco, settings = setup(tmp_path)
    try:
        assert enfileirar_nsu(settings, CNPJ, "15", "administrador", alta=True) == "000000000000015"
        fila = listar_fila(settings, CNPJ)
        assert len(fila) == 1
        assert fila[0]["prioridade"]
        assert fila[0]["status"] == "pendente"
        with pytest.raises(ValueError, match="numérico"):
            enfileirar_nsu(settings, CNPJ, "94650-1", "administrador", alta=True)
        with pytest.raises(ValueError, match="maxNSU"):
            enfileirar_nsu(settings, CNPJ, "21", "administrador", alta=True)
        assert cancelar_nsu(settings, CNPJ, "15")
        assert not cancelar_nsu(settings, CNPJ, "15")
        assert listar_fila(settings, CNPJ)[0]["status"] == "cancelado"
    finally:
        banco.fechar()


def test_alta_primeiro_depois_lacuna_e_fila_normal(tmp_path, monkeypatch):
    banco, settings = setup(tmp_path, lacuna=True)
    chamadas = []
    def fake(xml, *args):
        nsu = xml.split("<NSU>")[1].split("</NSU>")[0]
        chamadas.append(nsu)
        return retorno(nsu)
    monkeypatch.setattr("nfe_consulta.distribuicao._enviar_soap_windows", fake)
    try:
        enfileirar_nsu(settings, CNPJ, "15", "admin", alta=True)
        enfileirar_nsu(settings, CNPJ, "16", "admin", alta=False)
        recuperar_intervalos(banco, CNPJ, "33", None, limite=4)
        assert chamadas == [
            "000000000000015", "000000000000011",
            "000000000000012", "000000000000016"
        ]
        situacoes = {linha["nsu"]: linha["status"] for linha in listar_fila(settings, CNPJ)}
        assert situacoes["000000000000015"] == "recebido"
        assert situacoes["000000000000016"] == "recebido"
        assert banco.obter_estado(CNPJ) == ("000000000000020",) * 2
    finally:
        banco.fechar()


def test_prioridade_aciona_cons_nsu_sem_lacunas(tmp_path, monkeypatch):
    banco, settings = setup(tmp_path)
    chamadas = []
    def fake(xml, *args):
        assert "<consNSU>" in xml
        nsu = xml.split("<NSU>")[1].split("</NSU>")[0]
        chamadas.append(nsu)
        return retorno(nsu)
    monkeypatch.setattr("nfe_consulta.distribuicao._enviar_soap_windows", fake)
    try:
        enfileirar_nsu(settings, CNPJ, "18", "admin", alta=True)
        resumo = sincronizar(banco, CNPJ, "33", None, recuperar_lacunas=True)
        assert resumo.somente_recuperacao
        assert resumo.documentos_recuperados == 1
        assert chamadas == ["000000000000018"]
    finally:
        banco.fechar()


def test_indisponivel_e_bloqueio_de_reenvio_na_ultima_hora(tmp_path, monkeypatch):
    banco, settings = setup(tmp_path)
    monkeypatch.setattr("nfe_consulta.distribuicao._enviar_soap_windows",
                        lambda xml, *args: retorno("000000000000015", 137))
    try:
        enfileirar_nsu(settings, CNPJ, "15", "admin", alta=True)
        assert recuperar_intervalos(banco, CNPJ, "33", None) == 0
        assert listar_fila(settings, CNPJ)[0]["status"] == "indisponivel"
        with pytest.raises(ValueError, match="última hora"):
            enfileirar_nsu(settings, CNPJ, "15", "admin", alta=True)
    finally:
        banco.fechar()
