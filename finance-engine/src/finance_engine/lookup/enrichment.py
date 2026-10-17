"""Alertas cadastrais e sugestão de setor e especialidades pelo CNAE."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from finance_engine.lookup.models import CompanyRecord

# Divisões CNAE (2 primeiros dígitos) -> (setor sugerido, especialidades de consultoria mais comuns)
_CNAE_DIVISIONS: list[tuple[range, str, list[str]]] = [
    (range(1, 4), "Agropecuária", ["FIN", "TRIB"]),
    (range(5, 10), "Extração mineral", ["FIN", "TRIB", "JUR"]),
    (range(10, 34), "Indústria", ["FIN", "TRIB", "PROC"]),
    (range(35, 40), "Energia e saneamento", ["FIN", "JUR"]),
    (range(41, 44), "Construção", ["FIN", "TRIB", "JUR"]),
    (range(45, 48), "Comércio", ["FIN", "TRIB", "COM"]),
    (range(49, 54), "Transporte e logística", ["FIN", "TRIB", "PROC"]),
    (range(55, 57), "Alimentação e hospedagem", ["FIN", "COM", "PROC"]),
    (range(58, 64), "Tecnologia e comunicação", ["FIN", "TRIB", "TI"]),
    (range(64, 67), "Serviços financeiros", ["FIN", "JUR"]),
    (range(68, 69), "Imobiliário", ["FIN", "TRIB", "JUR"]),
    (range(69, 76), "Serviços profissionais", ["FIN", "TRIB", "COM"]),
    (range(77, 83), "Serviços administrativos", ["FIN", "RH", "PROC"]),
    (range(84, 85), "Administração pública", ["JUR"]),
    (range(85, 86), "Educação", ["FIN", "COM", "RH"]),
    (range(86, 89), "Saúde", ["FIN", "TRIB", "PROC"]),
    (range(90, 94), "Cultura, esporte e lazer", ["FIN", "MKT"]),
    (range(94, 100), "Outros serviços", ["FIN", "PROC"]),
]


def enrich(record: CompanyRecord, today: date | None = None) -> CompanyRecord:
    today = today or date.today()
    alerts: list[str] = []
    if record.status and record.status != "ATIVA":
        alerts.append(
            f"Situação cadastral na Receita: {record.status}. Confirme antes de contratar."
        )
    if record.opened_at and (today - record.opened_at).days < 365:
        alerts.append("Empresa aberta há menos de 1 ano: histórico financeiro curto.")
    if record.mei:
        alerts.append("Optante pelo MEI: faturamento limitado; avalie o desenquadramento.")
    elif record.simples:
        alerts.append(
            "Optante pelo Simples Nacional: compare com Lucro Presumido no diagnóstico tributário."
        )
    if not record.head_office:
        alerts.append("Este CNPJ é uma filial; cadastre também a matriz.")
    if any(p.kind == "PJ" for p in record.partners):
        alerts.append(
            "Há sócio pessoa jurídica: consulte o CNPJ dele para montar o grupo econômico."
        )
    sector, specialties = None, []
    if record.main_activity:
        division = int(record.main_activity.code[:2] or 0)
        for span, name, specs in _CNAE_DIVISIONS:
            if division in span:
                sector, specialties = name, specs
                break
    return replace(
        record, alerts=alerts, suggested_sector=sector, suggested_specialties=specialties
    )
