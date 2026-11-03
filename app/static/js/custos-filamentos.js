// Tela Custos, cards de filamento e de "Outros custos diretos": o lápis troca o valor exibido por um
// campo no próprio lugar. Valores em reais usam máscara monetária brasileira (só dígitos,
// preenchidos da direita para a esquerda como centavos); a taxa de perda aceita o percentual
// digitado. Enter (ou o botão de confirmar) salva pela rota de sempre, por fetch; Esc cancela e
// restaura o valor anterior. Perder o foco não salva. Sem JavaScript, o lápis segue para o
// formulário comum.
//
// Cada card traz o que precisa: data-csrf, data-titulo e, nos filamentos, data-chave; nos demais,
// data-rota (POST), data-campo (nome do campo) e data-tipo ("moeda" ou "percentual").
(function () {
  "use strict";

  const listas = document.querySelectorAll(".custos-cartoes-fil");
  if (!listas.length) return;

  const ROTA_AJUSTE = "/custos/insumos/ajuste";
  const MSG_FALHA = "Não foi possível salvar agora. Tente novamente em instantes.";
  const DIGITOS_INTEIROS = 9; // o servidor aceita valores abaixo de R$ 1.000.000.000
  const ICONE_OK =
    '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
  const ICONE_CANCELAR =
    '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M6 6l12 12M18 6L6 18"/></svg>';

  // ---- Máscara: tudo é texto e inteiros (nada de float) ----

  function apenasDigitos(texto) {
    return texto.replace(/\D/g, "");
  }

  // "12345" com escala 2 -> "R$ 123,45"; sem dígitos, vazio (aparece o texto de exemplo).
  function formatar(digitos, escala) {
    let d = digitos.replace(/^0+(?=\d)/, "").slice(0, DIGITOS_INTEIROS + escala);
    if (d === "") return "";
    d = d.padStart(escala + 1, "0");
    const inteiro = d.slice(0, d.length - escala).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    return "R$ " + inteiro + "," + d.slice(d.length - escala);
  }

  // O que vai para o servidor: "123,45" (vírgula decimal, sem separador de milhar).
  function paraEnvio(digitos, escala) {
    const d = digitos.replace(/^0+(?=\d)/, "").slice(0, DIGITOS_INTEIROS + escala);
    if (d === "") return "";
    const cheio = d.padStart(escala + 1, "0");
    return cheio.slice(0, cheio.length - escala) + "," + cheio.slice(cheio.length - escala);
  }

  // Valor salvo ("80,00", "1.234,5") -> dígitos e escala (no mínimo 2 casas, sem perder as salvas).
  function lerSalvo(texto) {
    const t = (texto || "").trim();
    if (!t) return { digitos: "", escala: 2 };
    const pos = t.lastIndexOf(",");
    const fracao = pos === -1 ? "" : apenasDigitos(t.slice(pos + 1));
    const escala = Math.max(2, fracao.length);
    const inteiro = apenasDigitos(pos === -1 ? t : t.slice(0, pos));
    return { digitos: inteiro + fracao.padEnd(escala, "0"), escala: escala };
  }

  // Texto colado -> dígitos. "100,00", "R$ 100,00", "1.234,56" e "100.5" respeitam o separador
  // decimal; "1.000" (milhar) é reais; só dígitos entra como se fosse digitado (centavos).
  function lerColado(texto, escala) {
    const t = texto.replace(/R\$|\s| /g, "");
    const decimal = t.match(new RegExp("^(.*?)[.,](\\d{1," + escala + "})$"));
    if (decimal && !/^\d{1,3}(\.\d{3})+$/.test(t)) {
      return apenasDigitos(decimal[1]) + decimal[2].padEnd(escala, "0");
    }
    if (/^\d{1,3}(\.\d{3})+$/.test(t)) return apenasDigitos(t) + "0".repeat(escala);
    return apenasDigitos(t);
  }

  function elemento(tag, atributos, filhos) {
    const e = document.createElement(tag);
    Object.entries(atributos || {}).forEach(([k, v]) => e.setAttribute(k, v));
    (filhos || []).forEach((f) => e.append(f));
    return e;
  }

  function botao(classe, rotulo, dica, icone) {
    const b = elemento("button", { type: "button", class: "custos-fil-botao " + classe, "aria-label": rotulo, title: dica });
    b.innerHTML = icone;
    return b;
  }

  // Valor digitado guardado só neste navegador (sessionStorage) enquanto a pessoa entra de novo.
  // Nunca é salvo sozinho: depois do login o campo reabre preenchido, e só Enter/confirmar salva.
  const CHAVE_PENDENTE = "custos-filamento-pendente";
  const VALIDADE_PENDENTE_MS = 30 * 60 * 1000;

  function guardarPendente(id, valor) {
    try {
      sessionStorage.setItem(CHAVE_PENDENTE, JSON.stringify({ id: id, valor: valor, em: Date.now() }));
    } catch (e) { /* sem armazenamento: só não recupera o valor */ }
  }

  function lerPendente() {
    try {
      const bruto = sessionStorage.getItem(CHAVE_PENDENTE);
      sessionStorage.removeItem(CHAVE_PENDENTE);
      const p = bruto && JSON.parse(bruto);
      return p && Date.now() - p.em < VALIDADE_PENDENTE_MS && /^[\d.,]{1,24}$/.test(p.valor) ? p : null;
    } catch (e) {
      return null;
    }
  }

  // Diz o que aconteceu quando a resposta não veio como o JSON esperado (sem afirmar que não salvou).
  // Só a resposta 401 do servidor ("expirou") indica sessão encerrada; redirecionamento solto não.
  function erroDeTransporte(resposta) {
    if (resposta.redirected) return "O servidor redirecionou a requisição de forma inesperada. Recarregue a página e confira o valor antes de tentar de novo.";
    if (resposta.status === 503) return "Serviço indisponível no momento. Tente novamente em instantes.";
    return "O servidor respondeu de forma inesperada (código " + resposta.status + "). Tente novamente.";
  }

  // Envia pela rota existente; devolve {ok, html?, erro?}. Cabeçalho X-Requested-With pede JSON.
  // ok = gravado. Gravado sem html significa que só a atualização visual do card não veio.
  // `url` e `valor` são opcionais: filamentos usam a rota de insumos (chave + valor); os demais
  // cards, a rota do próprio grupo, com o nome de campo que o card informa.
  async function enviar(card, valor, url) {
    const corpo = new URLSearchParams({ csrf: card.dataset.csrf });
    if (card.dataset.rota) {
      if (valor !== undefined) corpo.set(card.dataset.campo, valor);
    } else {
      corpo.set("chave", card.dataset.chave);
      if (valor !== undefined) corpo.set("valor", valor);
    }
    try {
      const resposta = await fetch(url || card.dataset.rota || ROTA_AJUSTE, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-Requested-With": "fetch",
          Accept: "application/json",
        },
        body: corpo,
      });
      let dados;
      try {
        dados = await resposta.json();
      } catch (e) {
        return { ok: false, erro: erroDeTransporte(resposta) };
      }
      if (resposta.status === 401 && dados.expirou) return { ok: false, expirou: true, login: dados.login, erro: dados.erro };
      return resposta.ok && dados.ok ? dados : { ok: false, erro: dados.erro || MSG_FALHA };
    } catch (e) {
      return { ok: false, erro: "Sem conexão com o servidor. Tente novamente." };
    }
  }

  function trocarCard(card, html) {
    const molde = document.createElement("template");
    molde.innerHTML = html.trim();
    const novo = molde.content.firstElementChild;
    card.replaceWith(novo);
    const lapis = novo.querySelector(".custos-lapis");
    if (lapis) lapis.focus();
  }

  // O card Uso da Impressora soma os custos por hora: acompanha a edição de cada um deles.
  function atualizarUsoDeMaquina(html) {
    const atual = document.getElementById("uso-maquina");
    if (!html || !atual) return;
    const molde = document.createElement("template");
    molde.innerHTML = html.trim();
    const novo = molde.content.firstElementChild;
    novo.querySelector("details").open = atual.querySelector("details").open;
    atual.replaceWith(novo);
  }

  // Gravado, mas o servidor não devolveu o card: mostra o valor salvo e avisa, sem recarregar.
  function salvoSemCard(card, textoValor, dataValor) {
    const valorEl = card.querySelector(".custos-fil-valor");
    valorEl.className = "custos-fil-valor custos-fil-valor-neutro";
    valorEl.dataset.valor = dataValor;
    valorEl.textContent = textoValor;
    valorEl.hidden = false;
    card.querySelectorAll(".custos-fil-comparacao, .custos-fil-aviso").forEach((e) => e.remove());
    const aviso = elemento("p", { class: "custos-fil-aviso", role: "status" });
    aviso.textContent = "Valor salvo. Não foi possível atualizar o card agora; recarregue a página para ver a comparação.";
    valorEl.after(aviso);
    const lapis = card.querySelector(".custos-lapis");
    if (lapis) lapis.focus();
  }

  // `pendente`: valor guardado antes do login (dígitos nos cards em reais; texto na taxa de perda).
  function abrirEdicao(card, pendente) {
    const existente = card.querySelector(".custos-fil-edicao input");
    if (existente) {
      existente.focus();
      return;
    }
    const percentual = card.dataset.tipo === "percentual";
    const valorEl = card.querySelector(".custos-fil-valor");
    const salvo = lerSalvo(valorEl.dataset.valor);
    const escala = salvo.escala;
    const id = card.id + "-inline";

    const rotulo = elemento("label", { class: "visualmente-oculto", for: id });
    rotulo.textContent = percentual
      ? "Percentual de " + card.dataset.titulo + " (%)"
      : card.dataset.titulo + " (R$)";
    const campo = elemento("input", {
      id: id,
      type: "text",
      inputmode: percentual ? "decimal" : "numeric", // teclado numérico no celular
      autocomplete: "off",
      maxlength: "24",
      placeholder: percentual ? "0" : "R$ 0," + "0".repeat(escala),
      class: "custos-fil-campo",
    });
    if (percentual) campo.value = pendente || valorEl.dataset.valor || "";
    else campo.value = formatar(pendente || salvo.digitos, escala); // preserva o valor salvo
    const ok = botao("custos-fil-ok", "Salvar " + card.dataset.titulo, "Salvar (Enter)", ICONE_OK);
    const cancelar = botao("custos-fil-cancelar", "Cancelar a edição", "Cancelar (Esc)", ICONE_CANCELAR);
    const progresso = elemento("span", { class: "custos-fil-progresso", role: "status" });
    const erro = elemento("p", { class: "campo-erro", id: id + "-erro", role: "alert" });
    erro.hidden = true;
    const linha = elemento("div", { class: "custos-fil-edicao" }, [rotulo, campo, ok, cancelar, progresso, erro]);

    valorEl.hidden = true;
    valorEl.after(linha);
    campo.focus();
    campo.select(); // digitar já substitui o valor
    if (pendente) {
      progresso.textContent = "Valor recuperado depois do login. Confira e confirme com Enter.";
    }

    function fechar() {
      linha.remove();
      valorEl.hidden = false;
      const lapis = card.querySelector(".custos-lapis");
      if (lapis) lapis.focus();
    }

    function ocupar(sim) {
      card.dataset.salvando = sim ? "1" : "";
      [campo, ok, cancelar].forEach((e) => { e.disabled = sim; });
      ok.setAttribute("aria-busy", sim ? "true" : "false");
      linha.classList.toggle("custos-fil-salvando", sim);
      progresso.textContent = sim ? "Salvando…" : "";
    }

    // Valor que vai para o servidor e o que guardar para recuperar depois do login.
    function valorDigitado() {
      if (percentual) return campo.value.trim();
      return paraEnvio(apenasDigitos(campo.value), escala);
    }

    async function salvar() {
      if (card.dataset.salvando) return; // evita envio duplicado
      erro.hidden = true;
      campo.removeAttribute("aria-invalid");
      campo.removeAttribute("aria-describedby");
      ocupar(true);
      const resultado = await enviar(card, valorDigitado());
      if (resultado.ok) {
        atualizarUsoDeMaquina(resultado.uso_maquina);
        if (resultado.html) trocarCard(card, resultado.html);
        else {
          const textoCampo = percentual ? campo.value.trim() + " %" : campo.value;
          const dataValor = valorDigitado();
          linha.remove();
          card.dataset.salvando = "";
          salvoSemCard(card, textoCampo, dataValor);
        }
        return;
      }
      if (resultado.expirou) {
        // Sessão encerrada de verdade: guarda o valor digitado e vai ao login, que volta para /custos.
        guardarPendente(card.id, percentual ? campo.value.trim() : apenasDigitos(campo.value));
        progresso.textContent = "Sessão expirada. Indo para o login…";
        window.location.assign(resultado.login || "/entrar?next=%2Fcustos");
        return;
      }
      ocupar(false);
      erro.textContent = resultado.erro; // o valor digitado fica no campo
      erro.hidden = false;
      campo.setAttribute("aria-invalid", "true");
      campo.setAttribute("aria-describedby", erro.id);
      campo.focus();
    }

    if (!percentual) {
      // Digitar: só dígitos, sempre preenchidos da direita para a esquerda.
      campo.addEventListener("input", () => {
        campo.value = formatar(apenasDigitos(campo.value), escala);
        campo.setSelectionRange(campo.value.length, campo.value.length);
      });

      campo.addEventListener("paste", (e) => {
        e.preventDefault();
        const colado = (e.clipboardData || window.clipboardData).getData("text");
        const digitos = lerColado(colado, escala);
        // Colar com o campo todo selecionado substitui; senão, acrescenta ao que já existe.
        const todo = campo.selectionStart === 0 && campo.selectionEnd === campo.value.length;
        campo.value = formatar(todo ? digitos : apenasDigitos(campo.value) + digitos, escala);
        campo.setSelectionRange(campo.value.length, campo.value.length);
      });
    }

    campo.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        salvar();
      } else if (e.key === "Escape") {
        e.preventDefault();
        if (!card.dataset.salvando) fechar();
      }
    });
    ok.addEventListener("click", salvar);
    cancelar.addEventListener("click", () => { if (!card.dataset.salvando) fechar(); });
  }

  // Voltando do login: reabre o campo do card com o valor digitado, sem salvar sozinho.
  const pendente = lerPendente();
  if (pendente) {
    const card = document.getElementById(pendente.id);
    if (card && card.classList.contains("custos-fil")) {
      abrirEdicao(card, card.dataset.tipo === "percentual" ? pendente.valor : apenasDigitos(pendente.valor));
    }
  }

  listas.forEach((lista) => {
    lista.addEventListener("click", (e) => {
      const lapis = e.target.closest("a.custos-lapis");
      if (!lapis || !lista.contains(lapis)) return;
      e.preventDefault();
      abrirEdicao(lapis.closest(".custos-fil"));
    });

    // "Voltar ao calculado" (energia): mesma rota de sempre, por fetch, atualizando só o card.
    lista.addEventListener("submit", async (e) => {
      const form = e.target;
      if (!form.classList.contains("custos-voltar-valor")) return;
      e.preventDefault();
      const card = form.closest(".custos-fil");
      if (card.dataset.salvando) return;
      const botaoVoltar = form.querySelector("button");
      card.dataset.salvando = "1";
      botaoVoltar.disabled = true;
      botaoVoltar.textContent = "Voltando…";
      const resultado = await enviar(card, undefined, form.getAttribute("action"));
      if (resultado.ok) atualizarUsoDeMaquina(resultado.uso_maquina);
      if (resultado.ok && resultado.html) return trocarCard(card, resultado.html);
      if (resultado.expirou) return window.location.assign(resultado.login || "/entrar?next=%2Fcustos");
      card.dataset.salvando = "";
      botaoVoltar.disabled = false;
      botaoVoltar.textContent = resultado.ok ? "Voltar ao calculado" : "Voltar ao calculado";
      let aviso = form.querySelector(".campo-erro");
      if (!aviso) {
        aviso = elemento("p", { class: "campo-erro", role: "alert" });
        form.append(aviso);
      }
      aviso.textContent = resultado.ok
        ? "Valor restaurado, mas não foi possível atualizar o card agora. Recarregue a página."
        : resultado.erro;
    });
  });
})();
