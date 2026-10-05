// Executor local dos testes SQL: aplica as migrations deste checkout num PostgreSQL em memória
// (PGlite) e roda os arquivos de teste. Nada se conecta ao banco remoto nem grava em disco.
//
// Uso (na raiz do projeto, depois de `npm install`):
//   npm run test:sql                                    -> supabase/tests/estoque_test.sql
//   node supabase/tests/executar_pglite.mjs <teste.sql> [...]
//   node supabase/tests/executar_pglite.mjs --chamadas entrada.json --listagem saida.json
//     Executa as chamadas RPC [{funcao, parametros}] por nome de parâmetro (como o PostgREST)
//     num estoque vazio e grava o resultado de listar_estoque() em saida.json.
//
// Cada arquivo de teste precisa terminar devolvendo uma linha com a coluna `resultado`
// (ex.: 'estoque_test: ok'); qualquer erro interrompe com código de saída 1.
//
// O ambiente do Supabase é simulado só no que as migrations usam: os papéis anon,
// authenticated e service_role, os privilégios padrão que o Supabase concede no schema public
// e a tabela storage.buckets. Não substitui rodar o teste no Supabase real depois de aplicar
// a migration (por pedido explícito).

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { PGlite } from "@electric-sql/pglite";

const RAIZ = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const PADRAO = ["supabase/tests/estoque_test.sql"];

function lerArgumentos(argv) {
  const opcoes = { testes: [], chamadas: null, listagem: null };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--chamadas") opcoes.chamadas = argv[++i];
    else if (argv[i] === "--listagem") opcoes.listagem = argv[++i];
    else opcoes.testes.push(argv[i]);
  }
  if (!opcoes.testes.length && !opcoes.chamadas) opcoes.testes = PADRAO;
  if (Boolean(opcoes.chamadas) !== Boolean(opcoes.listagem)) {
    throw new Error("--chamadas e --listagem são usados juntos");
  }
  return opcoes;
}

async function prepararBanco() {
  const db = new PGlite();
  await db.exec(`
    create role anon nologin;
    create role authenticated nologin;
    create role service_role nologin bypassrls;
    grant usage on schema public to anon, authenticated, service_role;
    alter default privileges in schema public
      grant all on tables to anon, authenticated, service_role;
    alter default privileges in schema public
      grant all on sequences to anon, authenticated, service_role;
    alter default privileges in schema public
      grant all on functions to anon, authenticated, service_role;
    create schema storage;
    create table storage.buckets (
      id text primary key, name text not null, public boolean,
      file_size_limit bigint, allowed_mime_types text[]
    );
  `);
  const pasta = path.join(RAIZ, "supabase", "migrations");
  for (const arquivo of fs.readdirSync(pasta).filter((a) => a.endsWith(".sql")).sort()) {
    await db.exec(fs.readFileSync(path.join(pasta, arquivo), "utf8"));
    console.log(`migration aplicada: ${arquivo}`);
  }
  return db;
}

async function rodarTeste(db, arquivo) {
  const resultados = await db.exec(fs.readFileSync(path.resolve(RAIZ, arquivo), "utf8"));
  const linha = resultados.flatMap((r) => r.rows).find((l) => l.resultado);
  if (!linha) throw new Error(`${arquivo} não chegou ao fim`);
  console.log(linha.resultado);
}

async function rodarChamadas(db, entrada, saida) {
  await db.exec("truncate public.estoque_filamentos, public.estoque_itens restart identity");
  for (const { funcao, parametros } of JSON.parse(fs.readFileSync(entrada, "utf8"))) {
    if (!/^[a-z_]+$/.test(funcao)) throw new Error(`nome de função inválido: ${funcao}`);
    const nomes = Object.keys(parametros);
    if (!nomes.every((n) => /^p_[a-z_]+$/.test(n))) throw new Error(`parâmetros: ${nomes}`);
    // Valores sem tipo, como o PostgREST envia: o banco converte para o tipo do parâmetro.
    const sql = `select public.${funcao}(${nomes.map((n, i) => `${n} => $${i + 1}`).join(", ")})`;
    const valores = nomes.map((n) => (parametros[n] === null ? null : String(parametros[n])));
    await db.query(sql, valores);
    console.log(`chamada: ${funcao}(${nomes.join(", ")})`);
  }
  const { rows } = await db.query("select public.listar_estoque() as dados");
  fs.writeFileSync(saida, JSON.stringify(rows[0].dados));
}

try {
  const opcoes = lerArgumentos(process.argv.slice(2));
  const db = await prepararBanco();
  for (const arquivo of opcoes.testes) await rodarTeste(db, arquivo);
  if (opcoes.chamadas) await rodarChamadas(db, opcoes.chamadas, opcoes.listagem);
  await db.close();
} catch (erro) {
  console.error(`ERRO: ${erro.message}${erro.where ? `\n${erro.where}` : ""}`);
  process.exit(1);
}
