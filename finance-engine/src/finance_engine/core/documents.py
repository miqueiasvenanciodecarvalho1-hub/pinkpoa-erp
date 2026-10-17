"""Validação de CPF/CNPJ (dígitos verificadores)."""

from __future__ import annotations


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _check_digit(digits: str, weights: list[int]) -> str:
    total = sum(int(d) * w for d, w in zip(digits, weights, strict=False))
    rest = total % 11
    return "0" if rest < 2 else str(11 - rest)


def is_valid_cpf(value: str) -> bool:
    cpf = _digits(value)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    first = _check_digit(cpf[:9], list(range(10, 1, -1)))
    second = _check_digit(cpf[:10], list(range(11, 1, -1)))
    return cpf[-2:] == first + second


def is_valid_cnpj(value: str) -> bool:
    cnpj = _digits(value)
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    first = _check_digit(cnpj[:12], w1)
    second = _check_digit(cnpj[:13], [6, *w1])
    return cnpj[-2:] == first + second
