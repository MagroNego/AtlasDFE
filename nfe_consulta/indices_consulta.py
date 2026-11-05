"""Índices das consultas locais, criados apenas ao preparar o banco para escrita."""


def criar_indices_manifestacoes(conexao) -> None:
    # As expressões correspondem às usadas nos filtros e na ordenação.
    # NSU continua TEXT no banco e é ordenado numericamente, inclusive em legados.
    conexao.execute("""CREATE INDEX IF NOT EXISTS idx_manifestacoes_consulta_ordem
        ON manifestacoes(cnpj, data_evento DESC, CAST(nsu AS INTEGER) DESC, id DESC)""")
    conexao.execute("""CREATE INDEX IF NOT EXISTS idx_manifestacoes_consulta_numero
        ON manifestacoes(cnpj, CAST(SUBSTR(chave,26,9) AS INTEGER),
                         data_evento DESC, CAST(nsu AS INTEGER) DESC, id DESC)""")
