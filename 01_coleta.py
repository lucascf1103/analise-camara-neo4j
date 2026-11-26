import os
import json
import time
import requests
import pandas as pd
from tqdm import tqdm

BASE_API = "https://dadosabertos.camara.leg.br/api/v2"
BASE_ARQ = "https://dadosabertos.camara.leg.br/arquivos"
ANO = 2025
LEGISLATURA = 57

DIR_ORIG = "dados_originais"
DIR_TRAT = "dados_tratados"
os.makedirs(DIR_ORIG, exist_ok=True)
os.makedirs(DIR_TRAT, exist_ok=True)

session = requests.Session()
session.headers.update({"Accept": "application/json", "User-Agent": "TrabalhoNeo4jAcademico/1.0"})


def baixar_arquivo(url, destino):
    if os.path.exists(destino):
        print(f"[OK] Já existe localmente: {destino}")
        return
    print(f"[BAIXANDO] {url}")
    resp = session.get(url, stream=True, timeout=120)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length", 0))
    with open(destino, "wb") as f, tqdm(total=total, unit="B", unit_scale=True, desc=os.path.basename(destino)) as bar:
        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)
                bar.update(len(chunk))


def get_json_seguro(url, params=None, tentativas=3):
    for i in range(tentativas):
        try:
            r = session.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
        except requests.RequestException:
            time.sleep(1 + i)
    return None


def coletar_paginado(url, params=None):
    itens = []
    next_url = url
    next_params = params
    while next_url:
        data = get_json_seguro(next_url, params=next_params)
        if not data:
            break
        itens.extend(data.get("dados", []))
        next_url = None
        next_params = None
        for link in data.get("links", []):
            if link.get("rel") == "next":
                next_url = link.get("href")
                break
    return itens


def extrair_id_deputado(row):
    if isinstance(row.get("deputado_"), dict) and row["deputado_"].get("id"):
        return row["deputado_"]["id"]
    for k in ("idDeputadoAutor", "deputado_id", "idDeputado"):
        if row.get(k):
            return row[k]
    uri = row.get("uriAutor") or ""
    if "/deputados/" in uri:
        try:
            return int(uri.rstrip("/").split("/")[-1])
        except ValueError:
            return None
    return None


def extrair_id_proposicao(row):
    if isinstance(row.get("proposicao_"), dict) and row["proposicao_"].get("id"):
        return row["proposicao_"]["id"]
    for k in ("idProposicao", "proposicao_id"):
        if row.get(k):
            return row[k]
    uri = row.get("uriProposicao") or ""
    if "/proposicoes/" in uri:
        try:
            return int(uri.rstrip("/").split("/")[-1])
        except ValueError:
            return None
    return None


def main():
    # 1. DEPUTADOS DA 57ª LEGISLATURA
    dep_path = os.path.join(DIR_ORIG, f"deputados_leg{LEGISLATURA}.json")
    if os.path.exists(dep_path):
        with open(dep_path, "r", encoding="utf-8") as f:
            deputados_raw = json.load(f)
    else:
        print(f"Coletando deputados da {LEGISLATURA}ª Legislatura...")
        deputados_raw = coletar_paginado(f"{BASE_API}/deputados", {"idLegislatura": LEGISLATURA, "itens": 100})
        with open(dep_path, "w", encoding="utf-8") as f:
            json.dump(deputados_raw, f, ensure_ascii=False, indent=2)

    df_deps = pd.DataFrame([
        {
            "id": int(d["id"]),
            "nome": d.get("nome", "").strip(),
            "siglaPartido": (d.get("siglaPartido") or "SEM_PARTIDO").strip(),
            "siglaUf": (d.get("siglaUf") or "NI").strip(),
            "idLegislatura": d.get("idLegislatura"),
            "email": d.get("email") or ""
        }
        for d in deputados_raw if d.get("id")
    ]).drop_duplicates(subset=["id"])
    ids_deputados_validos = set(df_deps["id"].tolist())

    # Partidos distintos
    df_partidos = df_deps[["siglaPartido"]].drop_duplicates().rename(columns={"siglaPartido": "sigla"})

    # 2. FRENTES PARLAMENTARES (57ª Legislatura)
    frentes_path = os.path.join(DIR_ORIG, f"frentes_leg{LEGISLATURA}.json")
    frentes_membros_path = os.path.join(DIR_ORIG, f"frentes_membros_leg{LEGISLATURA}.json")

    if os.path.exists(frentes_path):
        with open(frentes_path, "r", encoding="utf-8") as f:
            frentes_raw = json.load(f)
    else:
        print(f"Coletando frentes parlamentares da {LEGISLATURA}ª Legislatura...")
        frentes_raw = coletar_paginado(f"{BASE_API}/frentes", {"idLegislatura": LEGISLATURA})
        with open(frentes_path, "w", encoding="utf-8") as f:
            json.dump(frentes_raw, f, ensure_ascii=False, indent=2)

    if os.path.exists(frentes_membros_path):
        with open(frentes_membros_path, "r", encoding="utf-8") as f:
            frentes_membros_raw = json.load(f)
    else:
        print(f"Coletando membros de {len(frentes_raw)} frentes parlamentares...")
        frentes_membros_raw = []
        for fr in tqdm(frentes_raw, desc="Frentes"):
            fid = fr.get("id")
            if not fid:
                continue
            data = get_json_seguro(f"{BASE_API}/frentes/{fid}/membros")
            if data and data.get("dados"):
                for m in data["dados"]:
                    frentes_membros_raw.append({
                        "idFrente": int(fid),
                        "idDeputado": m.get("id"),
                        "tipo": (m.get("titulo") or "Membro").strip()
                    })
            time.sleep(0.05)
        with open(frentes_membros_path, "w", encoding="utf-8") as f:
            json.dump(frentes_membros_raw, f, ensure_ascii=False, indent=2)

    df_frentes = pd.DataFrame([
        {
            "id": int(fr["id"]),
            "titulo": (fr.get("titulo") or "").strip(),
            "idLegislatura": fr.get("idLegislatura")
        }
        for fr in frentes_raw if fr.get("id")
    ]).drop_duplicates(subset=["id"])

    df_frentes_dep = pd.DataFrame([
        {
            "idFrente": int(m["idFrente"]),
            "idDeputado": int(m["idDeputado"]),
            "tipo": m["tipo"]
        }
        for m in frentes_membros_raw
        if m.get("idDeputado") and int(m["idDeputado"]) in ids_deputados_validos
    ]).drop_duplicates(subset=["idFrente", "idDeputado"])

    # 3. ARQUIVOS EM LOTE DE 2025 (Votações, Votos, Proposições e Autores)
    arquivos = {
        "votacoes": f"{BASE_ARQ}/votacoes/json/votacoes-{ANO}.json",
        "votacoesVotos": f"{BASE_ARQ}/votacoesVotos/json/votacoesVotos-{ANO}.json",
        "votacoesProposicoes": f"{BASE_ARQ}/votacoesProposicoes/json/votacoesProposicoes-{ANO}.json",
        "proposicoes": f"{BASE_ARQ}/proposicoes/json/proposicoes-{ANO}.json",
        "proposicoesAutores": f"{BASE_ARQ}/proposicoesAutores/json/proposicoesAutores-{ANO}.json",
    }
    caminhos = {}
    for nome, url in arquivos.items():
        dest = os.path.join(DIR_ORIG, os.path.basename(url))
        baixar_arquivo(url, dest)
        caminhos[nome] = dest

    print("Processando votos e votações nominais...")
    with open(caminhos["votacoesVotos"], "r", encoding="utf-8") as f:
        votos_raw = json.load(f).get("dados", [])

    votos_limpos = []
    votacoes_com_voto = set()
    for v in votos_raw:
        id_vot = v.get("idVotacao")
        id_dep = extrair_id_deputado(v)
        tipo_voto = (v.get("voto") or v.get("tipoVoto") or "").strip()
        if id_vot and id_dep and int(id_dep) in ids_deputados_validos and tipo_voto:
            votos_limpos.append({
                "idVotacao": str(id_vot),
                "idDeputado": int(id_dep),
                "voto": tipo_voto,
                "dataRegistro": v.get("dataHoraVoto") or v.get("dataRegistroVoto") or ""
            })
            votacoes_com_voto.add(str(id_vot))

    with open(caminhos["votacoes"], "r", encoding="utf-8") as f:
        votacoes_raw = json.load(f).get("dados", [])

    votacoes_limpas = []
    ids_votacoes_validas = set()
    for vot in votacoes_raw:
        vid = str(vot.get("id", ""))
        if vid in votacoes_com_voto:
            votacoes_limpas.append({
                "id": vid,
                "data": (vot.get("data") or "")[:10],
                "siglaOrgao": (vot.get("siglaOrgao") or "").strip(),
                "descricao": (vot.get("descricao") or "").strip()[:300]
            })
            ids_votacoes_validas.add(vid)

    df_votacoes = pd.DataFrame(votacoes_limpas).drop_duplicates(subset=["id"])
    df_votos = pd.DataFrame([v for v in votos_limpos if v["idVotacao"] in ids_votacoes_validas])
    df_votos = df_votos.drop_duplicates(subset=["idVotacao", "idDeputado"])

    # Relação Votação -> Proposição
    with open(caminhos["votacoesProposicoes"], "r", encoding="utf-8") as f:
        vot_prop_raw = json.load(f).get("dados", [])

    vot_prop_limpo = []
    props_votadas = set()
    for vp in vot_prop_raw:
        vid = str(vp.get("idVotacao", ""))
        pid = extrair_id_proposicao(vp)
        if vid in ids_votacoes_validas and pid:
            pid = int(pid)
            vot_prop_limpo.append({"idVotacao": vid, "idProposicao": pid})
            props_votadas.add(pid)
    df_vot_prop = pd.DataFrame(vot_prop_limpo).drop_duplicates()

    # Proposições de 2025 (tipos principais) + todas as proposições votadas em 2025
    print("Processando proposições e autores...")
    tipos_interesse = {"PL", "PEC", "PLP", "MPV", "PDL", "PRC"}
    with open(caminhos["proposicoes"], "r", encoding="utf-8") as f:
        props_raw = json.load(f).get("dados", [])

    props_dict = {}
    for p in props_raw:
        pid = p.get("id")
        sigla = (p.get("siglaTipo") or "").strip()
        if pid and (sigla in tipos_interesse or int(pid) in props_votadas):
            props_dict[int(pid)] = {
                "id": int(pid),
                "siglaTipo": sigla,
                "numero": p.get("numero") or 0,
                "ano": p.get("ano") or ANO,
                "ementa": (p.get("ementa") or "").replace("\n", " ").strip()[:400]
            }

    # Buscar via API as proposições votadas em 2025 que foram criadas em anos anteriores
    faltantes = [pid for pid in props_votadas if pid not in props_dict]
    autores_extra = []
    if faltantes:
        print(f"Buscando {len(faltantes)} proposições de anos anteriores votadas em {ANO}...")
        for pid in tqdm(faltantes, desc="Proposições anteriores"):
            d = get_json_seguro(f"{BASE_API}/proposicoes/{pid}")
            if d and d.get("dados"):
                p = d["dados"]
                props_dict[pid] = {
                    "id": pid,
                    "siglaTipo": (p.get("siglaTipo") or "").strip(),
                    "numero": p.get("numero") or 0,
                    "ano": p.get("ano") or 0,
                    "ementa": (p.get("ementa") or "").replace("\n", " ").strip()[:400]
                }
            au = get_json_seguro(f"{BASE_API}/proposicoes/{pid}/autores")
            if au and au.get("dados"):
                for a in au["dados"]:
                    dep_id = extrair_id_deputado(a)
                    if dep_id and int(dep_id) in ids_deputados_validos:
                        autores_extra.append({
                            "idProposicao": pid,
                            "idDeputado": int(dep_id),
                            "ordemAssinatura": a.get("ordemAssinatura") or 1
                        })
            time.sleep(0.03)

    df_props = pd.DataFrame(list(props_dict.values())).drop_duplicates(subset=["id"])
    ids_props_validas = set(df_props["id"].tolist())
    df_vot_prop = df_vot_prop[df_vot_prop["idProposicao"].isin(ids_props_validas)]

    with open(caminhos["proposicoesAutores"], "r", encoding="utf-8") as f:
        autores_raw = json.load(f).get("dados", [])

    autores_limpos = list(autores_extra)
    for a in autores_raw:
        pid = extrair_id_proposicao(a)
        did = extrair_id_deputado(a)
        if pid and did and int(pid) in ids_props_validas and int(did) in ids_deputados_validos:
            autores_limpos.append({
                "idProposicao": int(pid),
                "idDeputado": int(did),
                "ordemAssinatura": a.get("ordemAssinatura") or 1
            })
    df_autores = pd.DataFrame(autores_limpos).drop_duplicates(subset=["idProposicao", "idDeputado"])

    # Salvar CSVs tratados
    df_deps.to_csv(os.path.join(DIR_TRAT, "deputados.csv"), index=False)
    df_partidos.to_csv(os.path.join(DIR_TRAT, "partidos.csv"), index=False)
    df_frentes.to_csv(os.path.join(DIR_TRAT, "frentes.csv"), index=False)
    df_frentes_dep.to_csv(os.path.join(DIR_TRAT, "frentes_deputados.csv"), index=False)
    df_props.to_csv(os.path.join(DIR_TRAT, "proposicoes.csv"), index=False)
    df_autores.to_csv(os.path.join(DIR_TRAT, "proposicoes_autores.csv"), index=False)
    df_votacoes.to_csv(os.path.join(DIR_TRAT, "votacoes.csv"), index=False)
    df_vot_prop.to_csv(os.path.join(DIR_TRAT, "votacoes_proposicoes.csv"), index=False)
    df_votos.to_csv(os.path.join(DIR_TRAT, "votacoes_votos.csv"), index=False)

    print("\n================ RESUMO DO DATASET COLETADO ================")
    print(f"Deputados (Legislatura {LEGISLATURA}): {len(df_deps)}")
    print(f"Partidos distintos:          {len(df_partidos)}")
    print(f"Frentes Parlamentares:       {len(df_frentes)}")
    print(f"Vínculos Frente-Deputado:    {len(df_frentes_dep)}")
    print(f"Proposições (PL/PEC/etc.):   {len(df_props)}")
    print(f"Vínculos Autor-Proposição:   {len(df_autores)}")
    print(f"Votações Nominais ({ANO}):    {len(df_votacoes)}")
    print(f"Vínculos Votação-Proposição: {len(df_vot_prop)}")
    print(f"Votos individuais ({ANO}):    {len(df_votos)}")
    print("============================================================")
    print("CSVs salvos com sucesso na pasta 'dados_tratados/'!")


if __name__ == "__main__":
    main()
