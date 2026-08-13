"""Dados de exemplo para você ver o sistema funcionando antes de cadastrar
as suas empresas de verdade.

Só roda quando o banco está VAZIO e quando armazenamento.criar_dados_exemplo
está true no config.yaml. Nunca sobrescreve nada.

As três empresas abaixo são fictícias, com CNPJs de teste válidos no dígito
verificador. Apague-as pela tela de Empresas quando quiser.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import func, select

from app.banco import sessao as abrir_sessao
from app.config import config
from app.modelos import (
    CertidaoExigida,
    Emissao,
    Empresa,
    OrigemEmissao,
    StatusEmissao,
    TipoCertidao,
)

logger = logging.getLogger(__name__)

EMPRESAS_EXEMPLO = [
    {
        "cnpj": "11222333000181",
        "razao_social": "EXEMPLO COMERCIO DE ALIMENTOS LTDA",
        "nome_fantasia": "Mercado Exemplo",
        "uf": "GO",
        "municipio": "Goiânia",
        "inscricao_estadual": "10.123.456-7",
        "inscricao_municipal": "123456",
        "regime_tributario": "Simples Nacional",
        "exigencias": ["FEDERAL", "CNDT", "FGTS", "ESTADUAL_GO"],
    },
    {
        "cnpj": "45997418000153",
        "razao_social": "MODELO SERVICOS CONTABEIS EIRELI",
        "nome_fantasia": "Modelo Serviços",
        "uf": "GO",
        "municipio": "Anápolis",
        "inscricao_estadual": None,
        "inscricao_municipal": "987654",
        "regime_tributario": "Lucro Presumido",
        # Sem inscrição estadual -> não exige a certidão estadual.
        "exigencias": ["FEDERAL", "CNDT", "FGTS"],
    },
    {
        "cnpj": "19131243000197",
        "razao_social": "TESTE INDUSTRIA E TRANSPORTES SA",
        "nome_fantasia": "Teste Transportes",
        "uf": "GO",
        "municipio": "Aparecida de Goiânia",
        "inscricao_estadual": "10.987.654-3",
        "inscricao_municipal": None,
        "regime_tributario": "Lucro Real",
        "exigencias": ["FEDERAL", "CNDT", "FGTS", "ESTADUAL_GO"],
    },
]


def _emissoes_exemplo(empresa_id: int, cnpj: str) -> list[Emissao]:
    """Emissões fictícias que produzem as quatro cores do semáforo."""
    hoje = date.today()

    if cnpj == "11222333000181":
        # Uma verde, uma amarela, uma cinza (falha), uma nunca consultada.
        return [
            Emissao(
                empresa_id=empresa_id,
                tipo_certidao=TipoCertidao.FEDERAL.value,
                status=StatusEmissao.NEGATIVA.value,
                data_emissao=hoje - timedelta(days=20),
                data_validade=hoje + timedelta(days=160),
                numero_certidao="EXEMPLO-FED-0001",
                origem=OrigemEmissao.ROBO.value,
            ),
            Emissao(
                empresa_id=empresa_id,
                tipo_certidao=TipoCertidao.FGTS.value,
                status=StatusEmissao.NEGATIVA.value,
                data_emissao=hoje - timedelta(days=22),
                data_validade=hoje + timedelta(days=8),
                numero_certidao="EXEMPLO-CRF-0001",
                origem=OrigemEmissao.ROBO.value,
            ),
            Emissao(
                empresa_id=empresa_id,
                tipo_certidao=TipoCertidao.CNDT.value,
                status=StatusEmissao.ERRO_SITE.value,
                mensagem_erro="Site do TST fora do ar no momento da consulta (exemplo).",
                origem=OrigemEmissao.ROBO.value,
            ),
        ]

    if cnpj == "45997418000153":
        # Uma vencida (vermelha) e uma positiva (vermelha).
        return [
            Emissao(
                empresa_id=empresa_id,
                tipo_certidao=TipoCertidao.CNDT.value,
                status=StatusEmissao.NEGATIVA.value,
                data_emissao=hoje - timedelta(days=200),
                data_validade=hoje - timedelta(days=20),
                numero_certidao="EXEMPLO-CNDT-0002",
                origem=OrigemEmissao.ROBO.value,
            ),
            Emissao(
                empresa_id=empresa_id,
                tipo_certidao=TipoCertidao.FEDERAL.value,
                status=StatusEmissao.POSITIVA.value,
                data_emissao=hoje - timedelta(days=5),
                data_validade=hoje + timedelta(days=175),
                numero_certidao="EXEMPLO-FED-0002",
                origem=OrigemEmissao.ROBO.value,
            ),
        ]

    # Terceira empresa: uma positiva com efeito de negativa (vale como válida)
    # e um captcha não resolvido.
    return [
        Emissao(
            empresa_id=empresa_id,
            tipo_certidao=TipoCertidao.ESTADUAL_GO.value,
            status=StatusEmissao.POSITIVA_COM_EFEITO_NEGATIVA.value,
            data_emissao=hoje - timedelta(days=10),
            data_validade=hoje + timedelta(days=50),
            numero_certidao="EXEMPLO-GO-0003",
            origem=OrigemEmissao.ROBO.value,
        ),
        Emissao(
            empresa_id=empresa_id,
            tipo_certidao=TipoCertidao.FEDERAL.value,
            status=StatusEmissao.CAPTCHA_FALHOU.value,
            mensagem_erro=(
                "Captcha da Receita não resolvido — nenhum serviço de captcha "
                "configurado no config.yaml. Emita à mão pela tela de Histórico."
            ),
            origem=OrigemEmissao.ROBO.value,
        ),
    ]


def criar_dados_exemplo(forcar: bool = False) -> int:
    """Cria as empresas de exemplo. Devolve quantas foram criadas."""
    if not forcar and not config.obter("armazenamento", "criar_dados_exemplo", padrao=True):
        return 0

    with abrir_sessao() as sessao:
        ja_tem = sessao.scalar(select(func.count()).select_from(Empresa)) or 0
        if ja_tem and not forcar:
            return 0

        criadas = 0
        for dados in EMPRESAS_EXEMPLO:
            existente = sessao.scalar(select(Empresa).where(Empresa.cnpj == dados["cnpj"]))
            if existente:
                continue

            exigencias = dados.pop("exigencias")
            empresa = Empresa(**dados, ativa=True, observacoes="Empresa de exemplo — pode apagar.")
            dados["exigencias"] = exigencias  # devolve para não quebrar em nova chamada

            sessao.add(empresa)
            sessao.flush()

            for tipo in exigencias:
                sessao.add(
                    CertidaoExigida(empresa_id=empresa.id, tipo_certidao=tipo, ativa=True)
                )

            for emissao in _emissoes_exemplo(empresa.id, empresa.cnpj):
                sessao.add(emissao)

            criadas += 1

        if criadas:
            logger.info("Criadas %d empresas de exemplo. Apague-as pela tela de Empresas.", criadas)
        return criadas
