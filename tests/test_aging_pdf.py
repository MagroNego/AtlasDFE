import subprocess
import sys

import pytest

from nfe_consulta.aging_pdf import convert_aging_pdf


def test_pdf_corrompido_retorna_erro_de_conversao():
    with pytest.raises(ValueError, match="Não foi possível ler o PDF"):
        convert_aging_pdf(b"%PDF-1.7\ninvalid")


def test_app_abre_sem_a_biblioteca_de_pdf():
    subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.modules['pdfplumber'] = None; import nfe_consulta.web.app"],
        check=True,
    )
