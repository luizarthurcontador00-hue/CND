"""Leitor do captcha do CNDT/TST — funciona offline, sem serviço pago.

POR QUE ISSO EXISTE
-------------------
O site do TST exige um captcha de 6 caracteres. Sem resolvê-lo, nenhuma CNDT
sai sozinha. Como não há serviço pago de captcha configurado, este módulo lê a
imagem localmente.

COMO FUNCIONA
-------------
O captcha do TST é fraco e muito regular, o que torna a leitura viável:
  - imagem sempre de 300x90 px;
  - 6 caracteres (minúsculas e dígitos) em fatias de posição fixa;
  - fonte única, sem rotação, sempre na mesma linha de base;
  - o ruído são círculos de traço fino (1 px), enquanto as letras têm traço
    grosso (3-4 px).

Passos:
  1. LIMPEZA — binariza e aplica uma abertura morfológica. Isso apaga os
     círculos de 1 px e mantém as letras.
  2. SEPARAÇÃO — corta a imagem nas 6 fatias fixas e pega a caixa da tinta de
     cada uma. Fatia fixa é mais confiável que componente conexo, porque a
     erosão às vezes parte uma letra em dois pedaços.
  3. COMPARAÇÃO — compara cada caractere, em tamanho real, com uma biblioteca
     de modelos e escolhe o mais parecido.

APRENDE COM O USO
-----------------
Sempre que uma emissão dá certo, sabemos que a resposta estava correta. Os 6
caracteres daquele captcha são então guardados na biblioteca (arquivo
dados/modelos_captcha_cndt.json). Ou seja: quanto mais o sistema roda, melhor
ele lê. Os modelos que vêm de fábrica são só o ponto de partida.
"""

from __future__ import annotations

import json
import logging
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- constantes

LARGURA = 300
ALTURA = 90
#: Fronteiras das 6 fatias, medidas em captchas reais do TST.
FRONTEIRAS = (0, 46, 97, 148, 200, 252, LARGURA)
LIMIAR_TINTA = 170
#: Mínimo de pixels escuros para considerar que há um caractere na fatia.
PIXELS_MIN = 12
#: Acima desta distância, a leitura é considerada duvidosa.
DISTANCIA_MAXIMA = 0.30
#: Quantos exemplos guardar por caractere (os mais recentes).
MAXIMO_POR_CLASSE = 14

ALFABETO = "abcdefghijklmnopqrstuvwxyz0123456789"

#: Biblioteca que acompanha o sistema.
ARQUIVO_FABRICA = Path(__file__).resolve().parent / "modelos_captcha_cndt.json"


# ------------------------------------------------------------------- limpeza


def limpar(origem) -> Image.Image:
    """Binariza e remove os círculos de ruído.

    A abertura morfológica (MaxFilter seguido de MinFilter) encolhe e depois
    devolve a espessura: linhas de 1 px somem, letras de 3-4 px sobrevivem.
    """
    if isinstance(origem, Image.Image):
        im = origem
    elif isinstance(origem, (bytes, bytearray)):
        im = Image.open(BytesIO(bytes(origem)))
    else:
        im = Image.open(str(origem))

    im = im.convert("L")
    if im.size != (LARGURA, ALTURA):
        im = im.resize((LARGURA, ALTURA), Image.LANCZOS)

    binaria = im.point(lambda v: 0 if v < LIMIAR_TINTA else 255)
    return binaria.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))


def _caixa_da_fatia(img: Image.Image, x_ini: int, x_fim: int):
    """Caixa que envolve a tinta dentro de uma fatia (ou None se estiver vazia)."""
    px = img.load()
    xs, ys = [], []
    for x in range(x_ini, min(x_fim, img.size[0])):
        for y in range(img.size[1]):
            if px[x, y] <= 128:
                xs.append(x)
                ys.append(y)
    if len(xs) < PIXELS_MIN:
        return None
    return (min(xs), min(ys), max(xs) + 1, max(ys) + 1)


def separar(origem) -> list[Image.Image | None]:
    """Devolve os 6 caracteres já limpos, em tamanho real (None onde não achou).

    Quando sobra um respingo de ruído colado na letra, a caixa sai maior que o
    normal e a comparação devolve distância alta. Isso não é problema: a leitura
    é marcada como duvidosa e o robô simplesmente pede outro captcha.
    """
    limpa = limpar(origem)
    saida = []
    for k in range(6):
        caixa = _caixa_da_fatia(limpa, FRONTEIRAS[k], FRONTEIRAS[k + 1])
        saida.append(limpa.crop(caixa) if caixa else None)
    return saida


# ------------------------------------------------------- modelos e comparação


def _para_bits(img: Image.Image) -> str:
    """Assinatura do caractere: '1' onde há tinta."""
    return "".join("1" if v <= 128 else "0" for v in img.convert("L").tobytes())


def _de_bits(bits: str, largura: int, altura: int) -> Image.Image:
    img = Image.new("L", (largura, altura), 255)
    px = img.load()
    for i, b in enumerate(bits):
        if b == "1":
            px[i % largura, i // largura] = 0
    return img


def distancia(a: Image.Image, b: Image.Image) -> float:
    """Fração de pixels diferentes entre dois caracteres (0 = idênticos).

    Tamanhos muito diferentes já eliminam o candidato: como a fonte do captcha
    é sempre a mesma, a altura e a largura são informação de verdade, não ruído.
    """
    if abs(a.width - b.width) > 3 or abs(a.height - b.height) > 3:
        return 1.0

    largura = max(a.width, b.width)
    altura = max(a.height, b.height)
    pa = a.resize((largura, altura), Image.LANCZOS).point(lambda v: 0 if v < 150 else 255).load()
    pb = b.resize((largura, altura), Image.LANCZOS).point(lambda v: 0 if v < 150 else 255).load()

    diferentes = sum(
        1
        for x in range(largura)
        for y in range(altura)
        if (pa[x, y] <= 128) != (pb[x, y] <= 128)
    )
    return diferentes / (largura * altura)


class Biblioteca:
    """Modelos de cada caractere, com aprendizado a partir dos acertos."""

    def __init__(self, arquivo_usuario: Path | None = None):
        self.arquivo_usuario = arquivo_usuario
        self.modelos: dict[str, list[Image.Image]] = {}
        self._carregar(ARQUIVO_FABRICA)
        if arquivo_usuario and Path(arquivo_usuario).exists():
            self._carregar(Path(arquivo_usuario))

    # ------------------------------------------------------------- carga
    def _carregar(self, arquivo: Path) -> None:
        try:
            dados = json.loads(Path(arquivo).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning("Não consegui ler os modelos de captcha em %s: %s", arquivo, e)
            return

        for letra, itens in (dados.get("modelos") or {}).items():
            for item in itens:
                try:
                    img = _de_bits(item["bits"], item["largura"], item["altura"])
                except (KeyError, TypeError):
                    continue
                self.modelos.setdefault(letra, []).append(img)

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.modelos.values())

    @property
    def classes(self) -> int:
        return len(self.modelos)

    # --------------------------------------------------------- reconhecer
    def reconhecer(self, glifo: Image.Image | None) -> tuple[str | None, float]:
        """Melhor caractere para este glifo e a distância obtida."""
        if glifo is None:
            return None, 1.0

        melhor_letra, melhor_dist = None, 1.0
        for letra, exemplos in self.modelos.items():
            for exemplo in exemplos:
                d = distancia(glifo, exemplo)
                if d < melhor_dist:
                    melhor_letra, melhor_dist = letra, d
                    if d < 0.02:  # praticamente idêntico, não precisa procurar mais
                        return melhor_letra, melhor_dist
        return melhor_letra, melhor_dist

    # ------------------------------------------------------------ aprender
    def aprender(self, glifos: list[Image.Image | None], resposta: str) -> int:
        """Guarda os caracteres de um captcha que sabidamente deu certo."""
        if not self.arquivo_usuario or len(resposta) != 6:
            return 0

        novos = 0
        for glifo, letra in zip(glifos, resposta.lower()):
            if glifo is None or letra not in ALFABETO:
                continue
            exemplos = self.modelos.setdefault(letra, [])
            # Não guarda o que já está praticamente igual — evita inchar o arquivo.
            if any(distancia(glifo, e) < 0.05 for e in exemplos):
                continue
            exemplos.append(glifo)
            del exemplos[:-MAXIMO_POR_CLASSE]
            novos += 1

        if novos:
            self._salvar()
        return novos

    def _salvar(self) -> None:
        dados = {
            "descricao": "Modelos aprendidos com os captchas que deram certo.",
            "modelos": {
                letra: [
                    {"largura": im.width, "altura": im.height, "bits": _para_bits(im)}
                    for im in exemplos
                ]
                for letra, exemplos in self.modelos.items()
            },
        }
        try:
            caminho = Path(self.arquivo_usuario)
            caminho.parent.mkdir(parents=True, exist_ok=True)
            caminho.write_text(json.dumps(dados), encoding="utf-8")
        except OSError as e:
            logger.warning("Não consegui gravar os modelos aprendidos: %s", e)


# ---------------------------------------------------------------- interface


class LeituraCaptcha:
    """Resultado da leitura de um captcha."""

    def __init__(self, texto: str, distancias: list[float], glifos: list):
        self.texto = texto
        self.distancias = distancias
        self.glifos = glifos

    @property
    def completo(self) -> bool:
        return len(self.texto) == 6

    @property
    def confiavel(self) -> bool:
        """Todos os 6 caracteres foram reconhecidos com folga."""
        return self.completo and all(d <= DISTANCIA_MAXIMA for d in self.distancias)

    @property
    def pior_distancia(self) -> float:
        return max(self.distancias) if self.distancias else 1.0

    def __str__(self) -> str:
        marca = "confiável" if self.confiavel else f"duvidosa (pior {self.pior_distancia:.2f})"
        return f"{self.texto!r} — {marca}"


def ler(imagem, biblioteca: Biblioteca) -> LeituraCaptcha:
    """Lê o captcha inteiro. Devolve o texto e o quanto dá para confiar nele."""
    glifos = separar(imagem)
    letras, distancias = [], []
    for glifo in glifos:
        letra, d = biblioteca.reconhecer(glifo)
        letras.append(letra or "?")
        distancias.append(d)
    return LeituraCaptcha("".join(letras), distancias, glifos)
