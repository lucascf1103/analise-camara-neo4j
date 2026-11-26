import os
import pandas as pd
from neo4j import GraphDatabase
from tqdm import tqdm

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASS = "CamaraNeo4j2026"  # <-- Troque se você escolheu outra senha!
DIR_TRAT = "dados_tratados"


def carregar_em_lotes(session, query, df, batch_size=1000, desc="Importando"):
    records = df.fillna("").to_dict("records")
    for i in tqdm(range(0, len(records), batch_size), desc=desc):
        lote = records[i:i + batch_size]
        session.run(query, rows=lote)


def main():
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))

    with driver.session() as session:
        print("1. Criando constraints de unicidade e índices...")
        constraints = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (d:Deputado) REQUIRE d.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (p:Partido) REQUIRE p.sigla IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (f:Frente) REQUIRE f.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (pr:Proposicao) REQUIRE pr.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (v:Votacao) REQUIRE v.id IS UNIQUE",
        ]
        for c in constraints:
            session.run(c)

        # Nós
        df_partidos = pd.read_csv(os.path.join(DIR_TRAT, "partidos.csv"))
        carregar_em_lotes(session, """
            UNWIND $rows AS row
            MERGE (:Partido {sigla: row.sigla})
        """, df_partidos, desc="Partidos")

        df_deps = pd.read_csv(os.path.join(DIR_TRAT, "deputados.csv"))
        carregar_em_lotes(session, """
            UNWIND $rows AS row
            MERGE (d:Deputado {id: toInteger(row.id)})
            SET d.nome = row.nome,
                d.siglaUf = row.siglaUf,
                d.siglaPartido = row.siglaPartido,
                d.email = row.email
            WITH d, row
            MATCH (p:Partido {sigla: row.siglaPartido})
            MERGE (d)-[:FILIADO_A]->(p)
        """, df_deps, desc="Deputados + FILIADO_A")

        df_frentes = pd.read_csv(os.path.join(DIR_TRAT, "frentes.csv"))
        carregar_em_lotes(session, """
            UNWIND $rows AS row
            MERGE (f:Frente {id: toInteger(row.id)})
            SET f.titulo = row.titulo,
                f.idLegislatura = toInteger(row.idLegislatura)
        """, df_frentes, desc="Frentes")

        df_props = pd.read_csv(os.path.join(DIR_TRAT, "proposicoes.csv"))
        carregar_em_lotes(session, """
            UNWIND $rows AS row
            MERGE (pr:Proposicao {id: toInteger(row.id)})
            SET pr.siglaTipo = row.siglaTipo,
                pr.numero = toInteger(row.numero),
                pr.ano = toInteger(row.ano),
                pr.ementa = row.ementa
        """, df_props, desc="Proposições")

        df_votacoes = pd.read_csv(os.path.join(DIR_TRAT, "votacoes.csv"))
        carregar_em_lotes(session, """
            UNWIND $rows AS row
            MERGE (v:Votacao {id: toString(row.id)})
            SET v.data = row.data,
                v.siglaOrgao = row.siglaOrgao,
                v.descricao = row.descricao
        """, df_votacoes, desc="Votações")

        # Relacionamentos
        df_frentes_dep = pd.read_csv(os.path.join(DIR_TRAT, "frentes_deputados.csv"))
        carregar_em_lotes(session, """
            UNWIND $rows AS row
            MATCH (d:Deputado {id: toInteger(row.idDeputado)})
            MATCH (f:Frente {id: toInteger(row.idFrente)})
            MERGE (d)-[r:INTEGRA]->(f)
            SET r.tipo = row.tipo
        """, df_frentes_dep, desc="Rel. INTEGRA")

        df_autores = pd.read_csv(os.path.join(DIR_TRAT, "proposicoes_autores.csv"))
        carregar_em_lotes(session, """
            UNWIND $rows AS row
            MATCH (d:Deputado {id: toInteger(row.idDeputado)})
            MATCH (pr:Proposicao {id: toInteger(row.idProposicao)})
            MERGE (d)-[r:AUTOR_DE]->(pr)
            SET r.ordemAssinatura = toInteger(row.ordemAssinatura)
        """, df_autores, desc="Rel. AUTOR_DE")

        df_vot_prop = pd.read_csv(os.path.join(DIR_TRAT, "votacoes_proposicoes.csv"))
        carregar_em_lotes(session, """
            UNWIND $rows AS row
            MATCH (v:Votacao {id: toString(row.idVotacao)})
            MATCH (pr:Proposicao {id: toInteger(row.idProposicao)})
            MERGE (v)-[:REFERENTE_A]->(pr)
        """, df_vot_prop, desc="Rel. REFERENTE_A")

        df_votos = pd.read_csv(os.path.join(DIR_TRAT, "votacoes_votos.csv"))
        carregar_em_lotes(session, """
            UNWIND $rows AS row
            MATCH (d:Deputado {id: toInteger(row.idDeputado)})
            MATCH (v:Votacao {id: toString(row.idVotacao)})
            MERGE (d)-[r:VOTOU]->(v)
            SET r.voto = row.voto,
                r.dataRegistro = row.dataRegistro
        """, df_votos, batch_size=2000, desc="Rel. VOTOU")

        print("\n=== VALIDAÇÃO NO NEO4J ===")
        res_nodes = session.run("MATCH (n) RETURN labels(n)[0] AS label, count(n) AS total ORDER BY total DESC")
        for r in res_nodes:
            print(f"Nó :{r['label']} -> {r['total']}")
        res_rels = session.run("MATCH ()-[r]->() RETURN type(r) AS tipo, count(r) AS total ORDER BY total DESC")
        for r in res_rels:
            print(f"Rel [:{r['tipo']}] -> {r['total']}")

    driver.close()
    print("\nImportação concluída com sucesso!")


if __name__ == "__main__":
    main()
