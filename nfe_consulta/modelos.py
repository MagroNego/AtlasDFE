from dataclasses import dataclass, field
from typing import Optional


class NfeConsultaErro(Exception):
    pass


class NfeChaveInvalidaErro(NfeConsultaErro):
    pass


class NfeErroComunicacao(NfeConsultaErro):
    pass


class NfeErroCertificado(NfeConsultaErro):
    pass


class NfeErroResposta(NfeConsultaErro):
    pass


class NfeConsumoIndevidoErro(NfeErroComunicacao):
    def __init__(self, message: str, *, ult_nsu: str | None = None,
                 ult_nsu_enviado: str | None = None):
        super().__init__(message)
        self.ult_nsu = ult_nsu
        self.ult_nsu_enviado = ult_nsu_enviado


class NfeLimiteConsultaErro(NfeConsultaErro):
    pass


@dataclass(frozen=True)
class Manifestacao:
    codigo: str
    descricao: str
    data: str
    protocolo: str
    nsu: str = ""
    schema: str = ""
    correcao: str = ""
    sequencia: str = ""


@dataclass(frozen=True)
class EventoCTe:
    chave_nfe: str
    chave_cte: str
    codigo: str
    data: str
    protocolo: str = ""
    sequencia: str = ""
    transportadora: str = ""
    cnpj_transportadora: str = ""
    nsu: str = ""
    schema: str = ""


@dataclass(frozen=True)
class InformacaoNota:
    chave: str
    emitente: str = ""
    cancelada: bool = False


@dataclass(frozen=True)
class ResultadoConsulta:
    chave: str
    status_codigo: int
    status_motivo: str
    protocolo_nfe: Optional[str]
    manifestacoes: tuple  # tuple[Manifestacao, ...]
    erro: Optional[str] = None
    ctes: tuple = ()
    cancelada: bool = False


@dataclass(frozen=True)
class CertificadoWindows:
    thumbprint: str
    subject: str
    issuer: str
    valid_to: str
    cnpj: Optional[str] = None
    store: str = "CurrentUser"
    file_path: Optional[str] = None
    file_password: Optional[str] = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class RetornoDistribuicao:
    status_codigo: int
    status_motivo: str
    ult_nsu: str
    max_nsu: str
    manifestacoes: tuple  # tuple[tuple[str, Manifestacao], ...]
    documentos_ignorados: int = 0
    ult_nsu_informado: bool = False
    informacoes_notas: tuple[InformacaoNota, ...] = ()
    eventos_cte: tuple[EventoCTe, ...] = ()
    documentos: tuple = ()  # (NSU, schema, XML, classificação)
