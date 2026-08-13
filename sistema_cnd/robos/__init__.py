"""Robôs de emissão de certidões.

Cada site é um módulo isolado neste pacote e todos expõem exatamente a mesma
função:

    def consultar(cnpj: str, contexto: dict) -> ResultadoConsulta

Sites de governo mudam de layout com frequência. Essa separação garante que
consertar um site não quebre os outros.
"""
