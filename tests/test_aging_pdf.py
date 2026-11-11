import subprocess
import sys

import pytest

from nfe_consulta.aging_pdf import convert_aging_pdf


def test_pdf_corrompido_retorna_erro_de_conversao():
    with pytest.raises(ValueError, match="Não foi possível ler o PDF"):
        convert_aging_pdf(b"%PDF-1.7\ninvalid")


def test_aging_carrega_leitor_embarcado_sem_pdfplumber():
    subprocess.run(
        [sys.executable, "-c",
         """
import sys

sys.modules["pdfplumber"] = None
import nfe_consulta.web.app
from nfe_consulta.aging_pdf import _READER_ARCHIVE
import pypdf

assert pypdf.__file__.startswith(_READER_ARCHIVE)
"""],
        check=True,
    )
