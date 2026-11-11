import pytest

from nfe_consulta.aging_pdf import convert_aging_pdf


def test_pdf_corrompido_retorna_erro_de_conversao():
    with pytest.raises(ValueError, match="Não foi possível ler o PDF"):
        convert_aging_pdf(b"%PDF-1.7\ninvalid")
