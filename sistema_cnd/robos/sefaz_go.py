"""Certidão de Débitos Estaduais — SEFAZ Goiás.

Emissão:  https://www.sefaz.go.gov.br/Certidao/Emissao/001frmEmiteCertidao_c.asp
Validação: https://www.sefaz.go.gov.br/Certidao/Validacao/001frmValidaCertidao_c.asp

Observações que valem para a implementação (Etapa 3):
  - Página ASP antiga, formulário simples. Provavelmente sem captcha.
  - Testar primeiro um POST direto via httpx; só cair para o Playwright se falhar.
  - A opção "para fins de espólio" fica sempre NÃO.
  - Se houver pendência, o site devolve certidão POSITIVA. Isso NÃO é erro:
    grava status POSITIVA, salva o PDF assim mesmo e gera alerta.
  - Validade fica por volta de 60 dias, mas deve ser lida do PDF.

ESTADO: esqueleto — implementação completa na Etapa 3.
"""

from __future__ import annotations

import logging

from robos.base import ResultadoConsulta, erro

logger = logging.getLogger(__name__)

URL_EMISSAO = "https://www.sefaz.go.gov.br/Certidao/Emissao/001frmEmiteCertidao_c.asp"
URL_VALIDACAO = "https://www.sefaz.go.gov.br/Certidao/Validacao/001frmValidaCertidao_c.asp"
TIPO = "ESTADUAL_GO"


def consultar(cnpj: str, contexto: dict) -> ResultadoConsulta:
    """Emite a certidão estadual de Goiás do CNPJ informado."""
    return erro(
        "Robô da SEFAZ-GO ainda não implementado (previsto para a Etapa 3).", permanente=True
    )
