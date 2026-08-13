"""Serviços pagos de resolução de captcha (opcionais).

O sistema NUNCA depende disto. Sem chave configurada no config.yaml, os robôs
usam a leitura local (robos/ocr_cndt.py) ou registram CAPTCHA_FALHOU e você
emite a certidão à mão pela tela de Histórico.

Isto existe para quem quiser 100% de automação e não se importar em pagar
alguns centavos por certidão. Suporta 2Captcha e Anti-Captcha, escolhidos em
captcha.provedor no config.yaml.
"""

from __future__ import annotations

import base64
import logging
import time

import httpx

logger = logging.getLogger(__name__)

ESPERA_ENTRE_CONSULTAS = 5


def resolver_por_servico(imagem: bytes, config) -> str | None:
    """Manda a imagem para o serviço configurado. Devolve o texto, ou None."""
    provedor = str(config.captcha.get("provedor", "nenhum")).lower().strip()
    chave = str(config.captcha.get("chave_api", "")).strip()
    limite = int(config.captcha.get("timeout_segundos", 180))

    if provedor in ("", "nenhum") or not chave:
        return None

    try:
        if provedor == "2captcha":
            return _dois_captcha(imagem, chave, limite)
        if provedor == "anticaptcha":
            return _anti_captcha(imagem, chave, limite)
    except Exception as e:
        logger.warning("O serviço de captcha (%s) falhou: %s", provedor, e)
        return None

    logger.warning(
        "Provedor de captcha desconhecido no config.yaml: %r. "
        "Use 'nenhum', '2captcha' ou 'anticaptcha'.",
        provedor,
    )
    return None


def _dois_captcha(imagem: bytes, chave: str, limite: int) -> str | None:
    with httpx.Client(timeout=40) as c:
        envio = c.post(
            "https://2captcha.com/in.php",
            data={
                "key": chave,
                "method": "base64",
                "body": base64.b64encode(imagem).decode(),
                "json": 1,
                "numeric": 0,
                "min_len": 6,
                "max_len": 6,
                "language": 2,
            },
        ).json()

        if envio.get("status") != 1:
            logger.warning("2Captcha recusou o envio: %s", envio.get("request"))
            return None

        identificador = envio["request"]
        fim = time.time() + limite
        while time.time() < fim:
            time.sleep(ESPERA_ENTRE_CONSULTAS)
            r = c.get(
                "https://2captcha.com/res.php",
                params={"key": chave, "action": "get", "id": identificador, "json": 1},
            ).json()
            if r.get("status") == 1:
                return str(r["request"]).strip()
            if r.get("request") != "CAPCHA_NOT_READY":
                logger.warning("2Captcha devolveu erro: %s", r.get("request"))
                return None

    logger.warning("2Captcha não respondeu dentro de %ss.", limite)
    return None


def _anti_captcha(imagem: bytes, chave: str, limite: int) -> str | None:
    with httpx.Client(timeout=40) as c:
        envio = c.post(
            "https://api.anti-captcha.com/createTask",
            json={
                "clientKey": chave,
                "task": {
                    "type": "ImageToTextTask",
                    "body": base64.b64encode(imagem).decode(),
                    "phrase": False,
                    "case": False,
                    "numeric": 0,
                    "minLength": 6,
                    "maxLength": 6,
                },
            },
        ).json()

        if envio.get("errorId"):
            logger.warning("Anti-Captcha recusou o envio: %s", envio.get("errorDescription"))
            return None

        tarefa = envio["taskId"]
        fim = time.time() + limite
        while time.time() < fim:
            time.sleep(ESPERA_ENTRE_CONSULTAS)
            r = c.post(
                "https://api.anti-captcha.com/getTaskResult",
                json={"clientKey": chave, "taskId": tarefa},
            ).json()
            if r.get("errorId"):
                logger.warning("Anti-Captcha devolveu erro: %s", r.get("errorDescription"))
                return None
            if r.get("status") == "ready":
                return str(r["solution"]["text"]).strip()

    logger.warning("Anti-Captcha não respondeu dentro de %ss.", limite)
    return None
