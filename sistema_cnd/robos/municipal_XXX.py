"""Certidão Municipal — prefeitura ainda a definir.

COMO PREENCHER DEPOIS
---------------------
Este arquivo já está ligado ao resto do sistema (registro.py, config.yaml,
tela de empresas). Quando a prefeitura for definida, basta preencher a função
consultar() abaixo — nada mais precisa mudar.

Passo a passo:
  1. Renomeie o arquivo para municipal_<cidade>.py (ex: municipal_goiania.py).
  2. Ajuste a linha "MUNICIPAL" em robos/registro.py para o novo nome.
  3. No config.yaml, mude certidoes.MUNICIPAL.ativo para true e ajuste
     validade_padrao_dias / janela_renovacao_dias.
  4. Implemente consultar() seguindo o mesmo padrão dos outros robôs:
       - baixar o PDF,
       - salvar com salvar_pdf(...),
       - ler a validade com validade_com_fallback(...),
       - devolver ResultadoConsulta.

Modelo de implementação (deixado comentado como referência):

    from robos.base import (
        STATUS_NEGATIVA, ResultadoConsulta, erro, limpar_cnpj,
        salvar_pdf, validade_com_fallback,
    )

    def consultar(cnpj, contexto):
        numero = limpar_cnpj(cnpj)
        # ... obter o PDF (httpx ou Playwright) ...
        destino = salvar_pdf(conteudo, contexto["pasta_certidoes"], numero, "MUNICIPAL")
        emissao, validade = validade_com_fallback(destino, contexto["regras"])
        return ResultadoConsulta(
            status=STATUS_NEGATIVA,
            caminho_pdf=str(destino),
            data_emissao=emissao,
            data_validade=validade,
        )
"""

from __future__ import annotations

import logging

from robos.base import ResultadoConsulta, erro

logger = logging.getLogger(__name__)

TIPO = "MUNICIPAL"

#: Preencha quando a prefeitura for definida.
URL_EMISSAO = ""
NOME_PREFEITURA = "(a definir)"


def consultar(cnpj: str, contexto: dict) -> ResultadoConsulta:
    """Emite a certidão municipal do CNPJ informado."""
    return erro(
        "Certidão municipal ainda não configurada. "
        "Informe qual é a prefeitura para eu preencher o robô "
        "(arquivo robos/municipal_XXX.py).",
        permanente=True,
    )
