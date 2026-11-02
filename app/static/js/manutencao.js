// Tela Manutenção. Há dois jeitos de editar, sem recarregar a página:
//  - Uma célula: duplo clique (ou toque, ou Enter com a célula focada) abre só aquele campo no
//    próprio lugar. Enter confirma, Esc cancela (nos comentários, Ctrl+Enter confirma e Enter
//    quebra a linha). Tab confirma, se algo mudou, e segue para a célula seguinte. O restante da
//    linha é reenviado como está; erros aparecem junto da célula e o texto digitado fica.
//  - A linha inteira: "Registrar manutenção" (linha nova) e o lápis da linha. O servidor devolve
//    a mesma linha de edição que a página já sabe desenhar; só grava após confirmar (Enter ou ✓).
// Nada é gravado a cada tecla nem ao sair de um campo. Sem JavaScript, os links seguem para a
// página com a linha em edição (formulário comum).
(function () {
  "use strict";

  const tabela = document.getElementById("manut-tabela");
  if (!tabela) return;

  const corpo = tabela.tBodies[0];
  const vazio = corpo.querySelector(".manut-vazio");
  const mensagem = document.getElementById("manut-mensagem");
  const MSG_FALHA = "Não foi possível salvar agora. Tente novamente em instantes.";
  const DIGITOS_MAXIMOS = 12; // numeric(12,2): até 9.999.999.999,99
  const ROTULOS = {
    data: "Data",
    tipo: "Tipo de manutenção",
    tempo: "Tempo de máquina parada",
    pecas: "Peça substituída",
    custo: "Custo (R$)",
    comentarios: "Comentários",
  };
  const TIPOS = { preventiva: "Preventiva", corretiva: "Corretiva" };

  // ---- Máscara do custo: só dígitos, preenchidos da direita para a esquerda como centavos ----

  function apenasDigitos(texto) {
    return texto.replace(/\D/g, "");
  }

  function limpar(digitos) {
    return digitos.replace(/^0+(?=\d)/, "").slice(0, DIGITOS_MAXIMOS);
  }

  // "12345" -> "R$ 123,45"; sem dígitos, vazio ("A definir"). Zero digitado é "R$ 0,00".
  function formatar(digitos) {
    const d = limpar(digitos);
    if (d === "") return "";
    const cheio = d.padStart(3, "0");
    const inteiro = cheio.slice(0, -2).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    return "R$ " + inteiro + "," + cheio.slice(-2);
  }

  function paraEnvio(digitos) {
    const d = limpar(digitos);
    if (d === "") return "";
    const cheio = d.padStart(3, "0");
    return cheio.slice(0, -2) + "," + cheio.slice(-2);
  }

  // "100,00", "R$ 100,00", "1.234,56" e "100.5" respeitam o separador decimal; "1.000" é reais;
  // só dígitos entra como se fosse digitado (centavos).
  function lerColado(texto) {
    const t = texto.replace(/R\$|\s| /g, "");
    const milhar = /^\d{1,3}(\.\d{3})+$/.test(t);
    const decimal = t.match(/^(.*?)[.,](\d{1,2})$/);
    if (decimal && !milhar) return apenasDigitos(decimal[1]) + decimal[2].padEnd(2, "0");
    if (milhar) return apenasDigitos(t) + "00";
    return apenasDigitos(t);
  }

  function mascararCusto(campo) {
    campo.value = formatar(apenasDigitos(campo.value));
    campo.addEventListener("input", () => {
      campo.value = formatar(apenasDigitos(campo.value));
      campo.setSelectionRange(campo.value.length, campo.value.length);
    });
    campo.addEventListener("paste", (e) => {
      e.preventDefault();
      const colado = (e.clipboardData || window.clipboardData).getData("text");
      const digitos = lerColado(colado);
      const todo = campo.selectionStart === 0 && campo.selectionEnd === campo.value.length;
      campo.value = formatar(todo ? digitos : apenasDigitos(campo.value) + digitos);
      campo.setSelectionRange(campo.value.length, campo.value.length);
    });
    campo.addEventListener("focus", () => campo.select());
  }

  // ---- Utilidades ----

  function anunciar(texto) {
    mensagem.textContent = texto;
  }

  function linhas() {
    return Array.from(corpo.querySelectorAll("tr[data-data]"));
  }

  function atualizarVazio() {
    if (vazio) vazio.hidden = linhas().length > 0;
  }

  function linhaEmEdicao() {
    return corpo.querySelector("tr.manut-editando");
  }

  function celulaEmEdicao() {
    return corpo.querySelector("td.manut-celula-editando");
  }

  function paraLinha(html) {
    const molde = document.createElement("template");
    molde.innerHTML = "<table><tbody>" + html.trim() + "</tbody></table>";
    return molde.content.querySelector("tr");
  }

  // Mais recentes primeiro (data e, na mesma data, id): a linha salva volta ao lugar certo.
  function posicionar(linha) {
    const data = linha.dataset.data;
    const id = Number(linha.dataset.id || Number.MAX_SAFE_INTEGER);
    const outras = linhas().filter((l) => l !== linha);
    const depois = outras.find((l) => {
      const d = l.dataset.data;
      return d < data || (d === data && Number(l.dataset.id || 0) < id);
    });
    if (depois) corpo.insertBefore(linha, depois);
    else if (outras.length) outras[outras.length - 1].after(linha);
    else corpo.insertBefore(linha, vazio);
  }

  function atualizarIndicadores(html) {
    if (!html) return false;
    const atual = document.getElementById("manut-indicadores");
    const molde = document.createElement("template");
    molde.innerHTML = html.trim();
    const bloco = molde.content.firstElementChild;
    if (!atual || !bloco) return false;
    atual.replaceWith(bloco);
    return true;
  }

  // Envia pela rota de sempre; devolve {ok, status, dados} ou {erroTransporte}.
  async function enviar(acao, valores, csrf) {
    const corpoEnvio = new URLSearchParams(valores);
    corpoEnvio.set("csrf", csrf);
    let resposta;
    try {
      resposta = await fetch(acao, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-Requested-With": "fetch",
          Accept: "application/json",
        },
        body: corpoEnvio,
      });
      return { resposta: resposta, dados: await resposta.json() };
    } catch (e) {
      return { erroTransporte: resposta ? "O servidor respondeu de forma inesperada. Tente novamente." : "Sem conexão com o servidor. Tente novamente." };
    }
  }

  // Valor guardado só neste navegador (sessionStorage) enquanto a pessoa entra de novo. Nunca é
  // salvo sozinho: depois do login a linha reabre preenchida, e só confirmar salva.
  const CHAVE_PENDENTE = "manutencao-pendente";
  const VALIDADE_PENDENTE_MS = 30 * 60 * 1000;

  function guardarPendente(alvo, valores) {
    try {
      sessionStorage.setItem(CHAVE_PENDENTE, JSON.stringify({ alvo: alvo, valores: valores, em: Date.now() }));
    } catch (e) { /* sem armazenamento: só não recupera */ }
  }

  function lerPendente() {
    try {
      const bruto = sessionStorage.getItem(CHAVE_PENDENTE);
      sessionStorage.removeItem(CHAVE_PENDENTE);
      const p = bruto && JSON.parse(bruto);
      return p && Date.now() - p.em < VALIDADE_PENDENTE_MS ? p : null;
    } catch (e) {
      return null;
    }
  }

  function sessaoExpirou(resposta, dados) {
    return resposta.status === 401 && dados.expirou;
  }

  // ---- Edição de uma célula ----

  const AJUDA_TEMPO = "hh:mm, 27h15min ou 02d05h";

  function criarEditor(campo, valor, id) {
    let editor;
    if (campo === "tipo") {
      editor = document.createElement("select");
      const opcoes = [["", "Escolha…"]].concat(Object.entries(TIPOS));
      opcoes.forEach(([v, t]) => {
        const o = document.createElement("option");
        o.value = v;
        o.textContent = t;
        editor.append(o);
      });
    } else if (campo === "comentarios") {
      editor = document.createElement("textarea");
      editor.rows = 3;
      editor.maxLength = 1000;
      editor.placeholder = "Opcional (Ctrl+Enter confirma)";
    } else {
      editor = document.createElement("input");
      editor.type = campo === "data" ? "date" : "text";
      editor.autocomplete = "off";
      if (campo === "tempo") {
        editor.placeholder = "00h00min";
        editor.maxLength = 20;
      }
      if (campo === "pecas") {
        editor.placeholder = "Vazio = sem troca";
        editor.maxLength = 300;
      }
      if (campo === "custo") {
        editor.inputMode = "numeric";
        editor.placeholder = "A definir";
        editor.maxLength = 24;
      }
    }
    editor.id = id;
    editor.className = "manut-celula-campo";
    editor.value = valor;
    return editor;
  }

  function valoresDaLinha(linha) {
    const v = {};
    linha.querySelectorAll("td[data-campo]").forEach((td) => { v[td.dataset.campo] = td.dataset.valor; });
    return v;
  }

  function editarCelula(td) {
    if (td.classList.contains("manut-celula-editando")) return;
    const aberta = celulaEmEdicao() || linhaEmEdicao();
    if (aberta) {
      const campoAberto = aberta.querySelector("input:not(:disabled), select, textarea");
      if (campoAberto) campoAberto.focus();
      anunciar("Termine ou cancele a edição aberta antes de abrir outra.");
      return;
    }
    const linha = td.parentElement;
    const campo = td.dataset.campo;
    const original = td.innerHTML;
    const id = "manut-" + linha.dataset.id + "-" + campo + "-celula";

    const rotulo = document.createElement("label");
    rotulo.className = "manut-rotulo";
    rotulo.htmlFor = id;
    rotulo.textContent = ROTULOS[campo];
    const editor = criarEditor(campo, td.dataset.valor, id);
    const erro = document.createElement("p");
    erro.className = "campo-erro manut-erro";
    erro.id = id + "-erro";
    erro.setAttribute("role", "alert");
    erro.hidden = true;
    const progresso = document.createElement("p");
    progresso.className = "manut-progresso";
    progresso.setAttribute("role", "status");
    const itens = [rotulo, editor];
    if (campo === "tempo") {
      const dica = document.createElement("p");
      dica.className = "manut-dica";
      dica.textContent = AJUDA_TEMPO;
      itens.push(dica);
    }
    itens.push(erro, progresso);

    td.classList.add("manut-celula-editando");
    td.removeAttribute("tabindex");
    td.replaceChildren(...itens);
    if (campo === "custo") {
      editor.value = formatar(apenasDigitos(td.dataset.valor));
      mascararCusto(editor);
    }
    editor.focus();
    if (editor.select && campo !== "data") editor.select();

    const inicial = editor.value;
    let salvando = false;

    function valorEnvio() {
      return campo === "custo" ? paraEnvio(apenasDigitos(editor.value)) : editor.value;
    }

    function fechar(foco) {
      if (salvando) return;
      td.classList.remove("manut-celula-editando");
      td.setAttribute("tabindex", "0");
      td.innerHTML = original;
      if (foco !== false) td.focus();
    }

    function mostrarErro(texto) {
      erro.textContent = texto;
      erro.hidden = !texto;
      if (texto) {
        editor.setAttribute("aria-invalid", "true");
        editor.setAttribute("aria-describedby", erro.id);
      } else {
        editor.removeAttribute("aria-invalid");
        editor.removeAttribute("aria-describedby");
      }
    }

    async function confirmar(depois) {
      if (salvando) return false; // evita envio duplicado
      if (editor.value === inicial) {
        fechar(depois === undefined);
        return true;
      }
      mostrarErro("");
      salvando = true;
      editor.disabled = true;
      progresso.textContent = "Salvando…";
      const valores = valoresDaLinha(linha);
      valores[campo] = valorEnvio();
      const { resposta, dados, erroTransporte } = await enviar("/manutencao/" + linha.dataset.id, valores, tabela.dataset.csrf);
      salvando = false;
      editor.disabled = false;
      progresso.textContent = "";
      if (erroTransporte) return mostrarErro(erroTransporte), editor.focus(), false;
      if (sessaoExpirou(resposta, dados)) {
        guardarPendente(linha.dataset.id, Object.assign(valores, { [campo]: valorEnvio() }));
        window.location.assign(dados.login || "/entrar?next=%2Fmanutencao");
        return false;
      }
      if (resposta.ok && dados.ok && dados.linha) {
        const nova = paraLinha(dados.linha);
        linha.replaceWith(nova);
        posicionar(nova);
        atualizarIndicadores(dados.indicadores);
        anunciar(ROTULOS[campo] + " salvo.");
        const proxima = nova.querySelector('td[data-campo="' + campo + '"]');
        if (proxima && depois === undefined) proxima.focus();
        return true;
      }
      // Erro de outro campo da mesma linha (por exemplo, um registro antigo incompleto): diz qual.
      const erros = dados.erros || {};
      const proprio = erros[campo];
      const outro = Object.keys(erros).find((n) => n !== campo);
      mostrarErro(proprio || (outro ? ROTULOS[outro] + ": " + erros[outro] : dados.erro || MSG_FALHA));
      editor.focus();
      return false;
    }

    editor.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        fechar();
      } else if (e.key === "Enter" && (campo !== "comentarios" || e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        confirmar();
      } else if (e.key === "Tab") {
        // Tab navega: confirma o que mudou e segue para a célula vizinha.
        const celulas = Array.from(corpo.querySelectorAll("td[data-campo]"));
        const posicao = celulas.indexOf(td) + (e.shiftKey ? -1 : 1);
        const destino = celulas[posicao];
        if (!destino) return; // fora da tabela: o Tab segue o caminho normal
        e.preventDefault();
        confirmar(true).then((ok) => {
          if (!ok) return;
          const id_destino = destino.parentElement.dataset.id;
          const atual = document.getElementById("manut-" + id_destino);
          const nova = atual && atual.querySelector('td[data-campo="' + destino.dataset.campo + '"]');
          if (nova) nova.focus();
        });
      }
    });
    // Mudar o tipo na lista já é a confirmação (não há texto para digitar).
    if (campo === "tipo") {
      editor.value = td.dataset.valor;
      editor.addEventListener("change", () => confirmar());
    }
  }

  // ---- Edição da linha inteira ----

  function mostrarErros(linha, erros, geral) {
    linha.querySelectorAll(".manut-erro").forEach((p) => { p.hidden = true; p.textContent = ""; });
    linha.querySelectorAll("[aria-invalid]").forEach((c) => {
      c.removeAttribute("aria-invalid");
      c.removeAttribute("aria-describedby");
    });
    let primeiro = null;
    Object.keys(erros || {}).forEach((nome) => {
      const campo = linha.querySelector('[name="' + nome + '"]');
      const slot = Array.from(linha.querySelectorAll(".manut-erro")).find((p) =>
        p.dataset.erroPara.split(" ").includes(nome),
      );
      if (slot && !slot.textContent) {
        slot.textContent = erros[nome];
        slot.hidden = false;
      }
      if (campo) {
        campo.setAttribute("aria-invalid", "true");
        if (slot) campo.setAttribute("aria-describedby", slot.id);
        if (!primeiro) primeiro = campo;
      }
    });
    const falha = linha.querySelector(".manut-falha");
    falha.textContent = geral || "";
    falha.hidden = !geral;
    if (primeiro) primeiro.focus();
  }

  function ocupar(linha, sim) {
    linha.dataset.salvando = sim ? "1" : "";
    linha.querySelectorAll("input, select, textarea, button").forEach((c) => { c.disabled = sim; });
    linha.querySelector(".manut-progresso").textContent = sim ? "Salvando…" : "";
  }

  function fechar(linha) {
    if (linha.dataset.salvando) return;
    const original = linha.manutOriginal;
    if (linha.dataset.alvo !== "nova" && !original) {
      window.location.assign("/manutencao"); // linha aberta pelo servidor: volta à página normal
      return;
    }
    if (original) linha.replaceWith(original);
    else linha.remove();
    atualizarVazio();
    const lapis = original && original.querySelector(".manut-editar");
    (lapis || document.getElementById("manut-adicionar")).focus();
  }

  function valoresDoFormulario(linha) {
    const dados = {};
    linha.querySelectorAll("[name]").forEach((c) => {
      if (c.name !== "csrf") dados[c.name] = c.name === "custo" ? paraEnvio(apenasDigitos(c.value)) : c.value;
    });
    return dados;
  }

  async function salvarLinha(linha) {
    if (linha.dataset.salvando) return; // evita envio duplicado
    const form = linha.querySelector("form");
    const valores = valoresDoFormulario(linha);
    const anterior = document.activeElement;
    mostrarErros(linha, {}, "");
    ocupar(linha, true);
    const { resposta, dados, erroTransporte } = await enviar(form.getAttribute("action"), valores, form.elements.csrf.value);
    if (erroTransporte) {
      ocupar(linha, false);
      mostrarErros(linha, {}, erroTransporte);
      return;
    }
    if (sessaoExpirou(resposta, dados)) {
      // Sessão encerrada de verdade: guarda o que foi digitado e vai ao login, que volta para cá.
      guardarPendente(linha.dataset.alvo, valores);
      linha.querySelector(".manut-progresso").textContent = "Sessão expirada. Indo para o login…";
      window.location.assign(dados.login || "/entrar?next=%2Fmanutencao");
      return;
    }
    if (resposta.ok && dados.ok) {
      const nova = dados.linha ? paraLinha(dados.linha) : null;
      if (!nova) {
        // Gravado, mas a linha não veio: recarregar mostra o que ficou salvo.
        window.location.assign("/manutencao?salvo=1");
        return;
      }
      linha.replaceWith(nova);
      posicionar(nova);
      atualizarVazio();
      anunciar(atualizarIndicadores(dados.indicadores) ? "Manutenção salva." : "Manutenção salva. Recarregue a página para atualizar os indicadores.");
      const lapis = nova.querySelector(".manut-editar");
      if (lapis) lapis.focus();
      return;
    }
    ocupar(linha, false);
    mostrarErros(linha, dados.erros || {}, dados.erro || MSG_FALHA);
    if (!Object.keys(dados.erros || {}).length && anterior && anterior.focus) anterior.focus();
  }

  function prepararLinha(linha, pendente) {
    const form = linha.querySelector("form");
    const custo = linha.querySelector('[name="custo"]');
    mascararCusto(custo);

    if (pendente) {
      Object.keys(pendente).forEach((nome) => {
        const campo = linha.querySelector('[name="' + nome + '"]');
        if (!campo || nome === "csrf") return;
        campo.value = nome === "custo" ? formatar(apenasDigitos(pendente[nome])) : pendente[nome];
      });
      linha.querySelector(".manut-progresso").textContent = "Dados recuperados depois do login. Confira e confirme.";
    }

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      salvarLinha(linha);
    });
    linha.addEventListener("keydown", (e) => {
      const alvo = e.target.tagName;
      if (e.key === "Escape") {
        e.preventDefault();
        fechar(linha);
      } else if (e.key === "Enter" && alvo !== "BUTTON" && alvo !== "A" && (alvo !== "TEXTAREA" || e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        salvarLinha(linha);
      }
    });
    linha.querySelector(".manut-cancelar").addEventListener("click", (e) => {
      e.preventDefault();
      fechar(linha);
    });
    const primeiro = linha.querySelector('[name="data"]');
    if (primeiro) primeiro.focus();
  }

  // Abre a linha de edição: "nova" cria uma linha no topo; um id troca a linha existente.
  async function abrir(alvo, pendente) {
    const aberta = linhaEmEdicao() || celulaEmEdicao();
    if (aberta) {
      const campo = aberta.querySelector("input:not(:disabled), select, textarea");
      if (campo) campo.focus();
      anunciar("Termine ou cancele a edição aberta antes de abrir outra.");
      return;
    }
    let html;
    try {
      const resposta = await fetch("/manutencao/linha?editar=" + encodeURIComponent(alvo), {
        credentials: "same-origin",
        headers: { Accept: "text/html" },
      });
      if (resposta.redirected) {
        window.location.assign("/entrar?next=%2Fmanutencao"); // sessão encerrada
        return;
      }
      if (!resposta.ok) throw new Error("falha");
      html = await resposta.text();
    } catch (e) {
      anunciar("Não foi possível abrir a edição agora. Tente novamente em instantes.");
      return;
    }
    const linha = paraLinha(html);
    if (!linha) return;
    if (alvo === "nova") {
      corpo.insertBefore(linha, corpo.firstElementChild);
      if (vazio) vazio.hidden = true;
    } else {
      const original = document.getElementById("manut-" + alvo);
      if (!original) return;
      linha.manutOriginal = original;
      original.replaceWith(linha);
    }
    prepararLinha(linha, pendente);
  }

  // ---- Eventos da tabela ----

  const toqueSimples = window.matchMedia("(pointer: coarse)");

  document.addEventListener("click", (e) => {
    const ligacao = e.target.closest("a[data-manut-editar], #manut-adicionar");
    if (ligacao) {
      if ((e.button && e.button !== 0) || e.metaKey || e.ctrlKey) return;
      e.preventDefault();
      if (ligacao.id === "manut-adicionar") abrir("nova");
      else abrir(ligacao.closest("tr").dataset.id);
      return;
    }
    // Toque: um toque abre o campo (não há duplo clique confortável no celular).
    const celula = e.target.closest("td[data-campo]");
    if (celula && corpo.contains(celula) && toqueSimples.matches && !e.target.closest("a, button, details")) {
      editarCelula(celula);
    }
  });

  corpo.addEventListener("dblclick", (e) => {
    const celula = e.target.closest("td[data-campo]");
    if (celula && !toqueSimples.matches) editarCelula(celula);
  });

  corpo.addEventListener("keydown", (e) => {
    const celula = e.target.closest && e.target.closest("td[data-campo]");
    if (!celula || e.target !== celula) return;
    if (e.key === "Enter" || e.key === "F2") {
      e.preventDefault();
      editarCelula(celula);
    }
  });

  // Página aberta já com a linha em edição (sem JavaScript, ou erro de validação do servidor):
  // o mesmo comportamento vale para essa linha.
  const editando = linhaEmEdicao();
  if (editando) prepararLinha(editando);

  // Voltando do login: reabre a linha com o que foi digitado, sem salvar sozinho.
  const pendente = lerPendente();
  if (pendente && !editando) {
    const valores = {
      data: pendente.valores.data, tipo: pendente.valores.tipo, tempo: pendente.valores.tempo,
      pecas: pendente.valores.pecas, custo: pendente.valores.custo, comentarios: pendente.valores.comentarios,
    };
    abrir(pendente.alvo, valores);
  }
})();
