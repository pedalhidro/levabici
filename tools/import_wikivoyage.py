#!/usr/bin/env python3
"""Gera data/reviews.ttl a partir do artigo do WikiVoyage
"Bicicletas como Bagagem em Ônibus Rodoviários do Brasil".

Fonte: https://pt.wikivoyage.org/wiki/Bicicletas_como_Bagagem_em_%C3%94nibus_Rodovi%C3%A1rios_do_Brasil
Licença do conteúdo derivado: CC BY-SA 4.0 (atribuição ao artigo e seus
autores). Cada avaliação gerada carrega prov:wasDerivedFrom apontando pro
artigo.

A tabela do artigo está TRANSCRITA abaixo (coluna a coluna) — rodar de
novo após atualizar a transcrição regenera o grafo. Mapeamentos:

  nota (1–5)  = clamp(round(1 + soma * 4/7), 1, 5)
                onde "soma" é a coluna 0–7 do próprio artigo;
  desmontar   = tabela "Tem que desmontar" + narrativa: caixa/case
                rígido ⇒ disassemblyFull; "sim" genérico ⇒ frontWheel;
  embalar     = "embalagem dura sim" ⇒ packingBoxed; "embalar sim" ⇒
                packingPartial; ambíguo ("às vezes", "recomendado") ⇒
                campo omitido (a nuance fica no corpo do texto);
  nota fiscal = "sim"/"em tese sim" ⇒ receiptStated (pedem falando);
  pagar       = grátis sim ⇒ paymentNone; taxa pequena/condicional ⇒
                paymentPartial; valor próximo de passagem ⇒ paymentFull;
  estresse    = omitido (o artigo não relata por viagem);
  data        = coluna "Atualizado" (AAAA-MM) ⇒ lb:tripDate AAAA-MM-01.

Uso:  python3 tools/import_wikivoyage.py > data/reviews.ttl
"""

import datetime
import sys

PAGE = ("https://pt.wikivoyage.org/wiki/"
        "Bicicletas_como_Bagagem_em_%C3%94nibus_Rodovi%C3%A1rios_do_Brasil")
GENERATED_AT = "2026-08-14T12:00:00-03:00"

# (slug, nome, atualizado AAAA-MM, soma, respostas, corpo, rota)
# respostas: dis=disassembly, pack=packing, rec=receipt, pay=payment,
# perm=permissionNeeded — None = omitir campo.
# rota: (nome_de, lat, lon, nome_para, lat, lon) ou None.
JCA_BODY = ("Regulamento explícito para bicicletas: basta tirar a roda "
            "dianteira e deixá-la presa junto à bici no bagageiro.")

COMPANIES = [
    ("viacao-cometa", "Viação Cometa", "2024-01", 7.0,
     dict(dis="FrontWheel", pack="None", rec="None", pay="None", perm=False),
     JCA_BODY + " (Grupo JCA.)", None),
    ("auto-viacao-1001", "Auto Viação 1001", "2024-01", 7.0,
     dict(dis="FrontWheel", pack="None", rec="None", pay="None", perm=False),
     JCA_BODY + " (Grupo JCA.)", None),
    ("catarinense", "Catarinense", "2024-01", 7.0,
     dict(dis="FrontWheel", pack="None", rec="None", pay="None", perm=False),
     JCA_BODY + " (Grupo JCA.) Há relato de bicicletas indo direto no "
     "bagageiro sem tirar roda.", None),
    ("expresso-do-sul", "Expresso do Sul", "2024-01", 7.0,
     dict(dis="FrontWheel", pack="None", rec="None", pay="None", perm=False),
     JCA_BODY + " (Grupo JCA.)", None),
    ("rapido-ribeirao", "Rápido Ribeirão", "2024-01", 7.0,
     dict(dis="FrontWheel", pack="None", rec="None", pay="None", perm=False),
     JCA_BODY + " (Grupo JCA.)", None),
    ("macaense", "Macaense", "2024-01", 7.0,
     dict(dis="FrontWheel", pack="None", rec="None", pay="None", perm=False),
     JCA_BODY + " (Grupo JCA.)", None),

    ("piracicabana", "Piracicabana", "2024-01", 6.0,
     dict(dis="None", pack="None", rec="None", pay="None", perm=None),
     "Sem regra pública escrita; na prática aceita de graça e sem "
     "desmontar. (Grupo Comporte.)", None),
    ("nossa-senhora-da-penha", "Nossa Senhora da Penha", "2024-01", 6.5,
     dict(dis="None", pack="None", rec="None", pay="None", perm=None),
     "Aceita de graça e sem desmontar; assina-se um termo no embarque. "
     "(Grupo Comporte.)", None),
    ("expresso-uniao", "Expresso União", "2024-01", 6.0,
     dict(dis="None", pack="None", rec="None", pay="None", perm=None),
     "Sem regra pública escrita; na prática aceita de graça e sem "
     "desmontar. (Grupo Comporte.)", None),
    ("viacao-prata", "Viação Prata", "2024-01", 6.0,
     dict(dis="None", pack="None", rec="None", pay="None", perm=None),
     "Sem regra pública escrita; na prática aceita de graça e sem "
     "desmontar. (Grupo Comporte.)", None),
    ("viacao-princesa", "Viação Princesa", "2024-01", 6.0,
     dict(dis="None", pack="None", rec="None", pay="None", perm=None),
     "Sem regra pública escrita; na prática aceita de graça e sem "
     "desmontar. (Grupo Comporte.)", None),

    ("viacao-atibaia", "Viação Atibaia", "2023-07", 5.5,
     dict(dis="None", pack="None", rec="None", pay="None", perm=True),
     "Se o bagageiro estiver vazio, a bicicleta pode ir montada; caso "
     "contrário pode ser preciso compactá-la.", None),
    ("viacao-santa-cruz", "Viação Santa Cruz", "2023-07", 5.5,
     dict(dis="None", pack="None", rec="None", pay="None", perm=True),
     "Política explícita, mas depende de confirmação do motorista no "
     "embarque e de bagageiro não lotado. A bici pode ir sem tirar roda.",
     None),
    ("valle-sul", "Valle Sul", "2023-07", 6.0,
     dict(dis="FrontWheel", pack="Partial", rec="None", pay="None", perm=None),
     "Oficialmente: selim abaixado, rodas desmontadas e embalagem de "
     "plástico-bolha ou papelão. Na prática basta tirar a roda dianteira "
     "e passar um saco plástico; às vezes aceitam a bici montada.", None),
    ("viacao-graciosa", "Viação Graciosa", "2023-07", 5.5,
     dict(dis="Full", pack="Partial", rec="None", pay="None", perm=None),
     "Bicicletas contam como 'encomenda' paga à parte, EXCETO se "
     "desmontadas e embaladas (tirar as duas rodas e passar um saco "
     "plástico), aí o transporte é grátis. Como encomenda, "
     "Curitiba–Morretes custava R$15 por bici em 2022.",
     ("Curitiba", -25.437, -49.269, "Morretes", -25.477, -48.834)),
    ("passaro-marrom", "Pássaro Marrom", "2024-01", 6.0,
     dict(dis="FrontWheel", pack="None", rec="None", pay="None", perm=None),
     "Sem política explícita; na prática basta tirar a roda dianteira.",
     None),

    ("viacao-garcia", "Viação Garcia", "2023-07", 3.5,
     dict(dis="FrontWheel", pack="Partial", rec="Stated", pay="None",
          perm=None),
     "Posição ambígua: precisa 'embalar' (nem que seja com sacolas) e "
     "pedem nota fiscal, apesar de nunca olharem. Às vezes deixam "
     "embarcar só tirando a roda dianteira. (Grupo Garcia.)", None),
    ("brasil-sul", "Brasil Sul", "2023-07", 3.5,
     dict(dis="FrontWheel", pack="Partial", rec="Stated", pay="None",
          perm=None),
     "Posição ambígua: precisa 'embalar' (nem que seja com sacolas) e "
     "pedem nota fiscal, apesar de nunca olharem. (Grupo Garcia.)", None),
    ("viacao-cetro", "Viação Cetro", "2023-07", 3.0,
     dict(dis="FrontWheel", pack="Partial", rec="None", pay="Full",
          perm=None),
     "Sem regulamento público; na prática o transporte é cobrado — "
     "R$100 em 2023.", None),

    ("eucatur", "Eucatur", "2023-07", 0.5,
     dict(dis="Full", pack="Boxed", rec="Stated", pay="Partial", perm=True),
     "Exigem caixa (sacos e embalagens moles não são aceitos), pedem "
     "nota fiscal e muitas vezes cobram. Na rota Curitiba–São Paulo "
     "houve embarques sem problema com mala-bike flexível.",
     ("Curitiba", -25.437, -49.269, "São Paulo (Terminal Tietê)",
      -23.516, -46.622)),
    ("expresso-nordeste", "Expresso Nordeste", "2022-12", 0.5,
     dict(dis="Full", pack="Boxed", rec="Stated", pay="Partial", perm=True),
     "Exigem caixa (sacos não são aceitos; talvez plástico-bolha). O "
     "transporte é pago como excesso de bagagem, cerca de 50% da "
     "passagem. Sem regulamento explícito.", None),
    ("viacao-aguia-branca", "Viação Águia Branca", "2024-01", 3.0,
     dict(dis="FrontWheel", pack="Partial", rec="None", pay="Partial",
          perm=None),
     "Sem regulamento público; na prática cobram se a bici não estiver "
     "embalada.", None),

    ("vale-do-tiete", "Vale do Tietê", "2024-01", 5.5,
     dict(dis="None", pack="None", rec="None", pay="None", perm=None),
     "Regulamento explícito: basta fixar a bici com elástico ou corda; "
     "às vezes basta colocá-la no bagageiro.", None),
    ("viacao-cambui", "Viação Cambuí", "2024-01", 5.5,
     dict(dis="None", pack="None", rec="None", pay="None", perm=None),
     "De graça e normalmente sem desmontar; sem regra pública escrita.",
     None),
    ("viacao-kaissara", "Viação Kaissara", "2024-01", 5.0,
     dict(dis="FrontWheel", pack="None", rec="None", pay="None", perm=None),
     "Basta tirar a roda dianteira. (Grupo Itapemirim.)", None),
    ("itapemirim", "Viação Itapemirim", "2024-01", 2.0,
     dict(dis="Full", pack="Boxed", rec="None", pay="Partial", perm=None),
     "Transporte pago e exige embalagem dura (caixa ou afins).", None),

    ("util", "UTIL", "2024-01", 5.0,
     dict(dis="FrontWheel", pack=None, rec="None", pay="None", perm=None),
     "Aceitam de graça, exceto em ônibus de dois andares; embalagem "
     "'recomendada'. Regra escrita. (Grupo Guanabara.)", None),
    ("expresso-guanabara", "Expresso Guanabara", "2024-01", 5.0,
     dict(dis="FrontWheel", pack=None, rec="None", pay="None", perm=None),
     "Aceitam de graça, exceto em ônibus de dois andares; embalagem "
     "'recomendada'. Regra escrita. (Grupo Guanabara.)", None),

    ("buser", "Buser", "2024-01", 5.0,
     dict(dis="FrontWheel", pack="Partial", rec="None", pay="None",
          perm=None),
     "Regra escrita: desmontar e embalar em plástico-bolha ou case "
     "rígido; de graça.", None),
    ("gontijo", "Gontijo", "2023-01", 4.0,
     dict(dis="FrontWheel", pack="None", rec="None", pay="Partial",
          perm=None),
     "Transporte pago; precisa desmontar. A 'regra escrita' é um post "
     "em rede social.", None),
    ("reunidas-paulista", "Reunidas Paulista", "2024-05", 6.0,
     dict(dis="None", pack="Partial", rec="None", pay="None", perm=None),
     "Regra escrita; de graça e sem desmontar, mas precisa embalar.",
     None),
    ("ouro-e-prata", "Ouro e Prata", "2024-07", 4.5,
     dict(dis=None, pack=None, rec="None", pay="None", perm=None),
     "De graça; a regra só existe 'no telefone'. Desmontar e embalar "
     "são exigidos às vezes.", None),
    ("emtram", "Emtram", "2024-09", 4.5,
     dict(dis="FrontWheel", pack="Partial", rec="None", pay="None",
          perm=None),
     "De graça, mas precisa desmontar e embalar; a regra só existe "
     "'no telefone'.", None),
    ("passaro-verde", "Pássaro Verde", "2025-02", 4.0,
     dict(dis="FrontWheel", pack="Partial", rec="None", pay="Partial",
          perm=None),
     "Regra escrita: bici desmontada e embalada em plástico-bolha ou "
     "bolsa-bike, mediante taxa de R$27.", None),
    ("flixbus", "FlixBus", "2025-02", 0.0,
     dict(dis=None, pack=None, rec=None, pay=None, perm=None),
     "Não levam bicicletas no Brasil.", None),
]

# Empresas de outros modais mantidas da semente original (sem avaliações
# por enquanto — aparecem no seletor do formulário).
EXTRA_COMPANIES = [
    ("latam", "LATAM Airlines Brasil", "Plane"),
    ("azul", "Azul Linhas Aéreas", "Plane"),
    ("cptm", "CPTM", "Train"),
    ("ccr-barcas", "CCR Barcas (Rio–Niterói)", "Ferry"),
]


def score(soma):
    return max(1, min(5, round(1 + soma * 4 / 7)))


def ttl_escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def emit(out):
    w = out.write
    w(f"""@prefix lb:     <https://id.pedalhidrografi.co/levabici/terms#> .
@prefix emp:    <https://id.pedalhidrografi.co/levabici/empresa/> .
@prefix av:     <https://id.pedalhidrografi.co/levabici/avaliacao/> .
@prefix schema: <https://schema.org/> .
@prefix prov:   <http://www.w3.org/ns/prov#> .
@prefix xsd:    <http://www.w3.org/2001/XMLSchema#> .

#################################################################
# levabici — grafo de dados (semente)
#
# GERADO por tools/import_wikivoyage.py — edite lá e regenere.
#
# Avaliações derivadas do artigo do WikiVoyage "Bicicletas como
# Bagagem em Ônibus Rodoviários do Brasil" (CC BY-SA 4.0; atribuição
# ao artigo e seus autores — cada avaliação carrega
# prov:wasDerivedFrom). A nota 1–5 é derivada da coluna "soma" (0–7)
# do próprio artigo; ver o cabeçalho do script para os mapeamentos.
#################################################################

""")
    for slug, name, *_ in COMPANIES:
        w(f"emp:{slug} a lb:Company ;\n")
        w(f'    schema:name "{ttl_escape(name)}" ;\n')
        w("    lb:mode lb:modeBus .\n\n")
    for slug, name, mode in EXTRA_COMPANIES:
        w(f"emp:{slug} a lb:Company ;\n")
        w(f'    schema:name "{ttl_escape(name)}" ;\n')
        w(f"    lb:mode lb:mode{mode} .\n\n")

    for slug, name, updated, soma, ans, body, route in COMPANIES:
        rid = f"av:wikivoyage-{slug}"
        nota = score(soma)
        w(f"## {name} — WikiVoyage, atualizado {updated}, soma {soma}/7\n")
        w(f"{rid} a lb:Review ;\n")
        w(f"    schema:itemReviewed emp:{slug} ;\n")
        w(f"    schema:reviewRating {rid}_rating ;\n")
        w(f"    lb:trip {rid}_trip ;\n")
        if ans.get("perm") is not None:
            w(f"    lb:permissionNeeded {'true' if ans['perm'] else 'false'} ;\n")
        if ans.get("dis"):
            w(f"    lb:disassemblyLevel lb:disassembly{ans['dis']} ;\n")
        if ans.get("pack"):
            w(f"    lb:packingLevel lb:packing{ans['pack']} ;\n")
        if ans.get("rec"):
            w(f"    lb:receiptRequirement lb:receipt{ans['rec']} ;\n")
        if ans.get("pay"):
            w(f"    lb:paymentLevel lb:payment{ans['pay']} ;\n")
        full_body = f"{body} — WikiVoyage (atualizado {updated}, soma {soma}/7)."
        w(f'    schema:reviewBody "{ttl_escape(full_body)}" ;\n')
        w(f"    prov:wasDerivedFrom <{PAGE}> ;\n")
        w(f'    prov:generatedAtTime "{GENERATED_AT}"^^xsd:dateTime .\n\n')

        w(f"{rid}_rating a schema:Rating ;\n")
        w(f"    schema:ratingValue {nota} ;\n")
        w("    schema:bestRating 5 ;\n    schema:worstRating 1 .\n\n")

        w(f"{rid}_trip a schema:BusTrip ;\n")
        end = " ;\n" if route else " .\n\n"
        w(f'    lb:tripDate "{updated}-01"^^xsd:date{end}')
        if route:
            from_name, flat, flon, to_name, tlat, tlon = route
            w(f"    lb:departurePlace {rid}_trip_from ;\n")
            w(f"    lb:arrivalPlace {rid}_trip_to .\n\n")
            w(f"{rid}_trip_from a schema:Place ;\n")
            w(f'    schema:name "{ttl_escape(from_name)}" ;\n')
            w(f"    schema:latitude {flat} ;\n    schema:longitude {flon} .\n\n")
            w(f"{rid}_trip_to a schema:Place ;\n")
            w(f'    schema:name "{ttl_escape(to_name)}" ;\n')
            w(f"    schema:latitude {tlat} ;\n    schema:longitude {tlon} .\n\n")


if __name__ == "__main__":
    emit(sys.stdout)
