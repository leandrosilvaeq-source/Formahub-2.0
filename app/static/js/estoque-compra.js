// Tela Estoque: janela Registrar Compra (abrir, linhas dinâmicas, máscaras, cálculos e envio).
//
// Os cálculos da tela são só uma prévia (valores inteiros em centavos e centigramas, para não
// acumular erro); o servidor valida e calcula tudo de novo com Decimal.
// Envio: multipart/form-data com cada campo da linha na posição i como item_{i}_{campo} e a foto
// como item_{i}_foto. A resposta é JSON: {destino} no sucesso; {erro, campos} quando não salva.
// Cancelar (botão, × ou Esc) fecha a janela sem enviar nada e descarta o que foi digitado.
(function () {
  "use strict";

  const janela = document.getElementById("compra-janela");
  if (!janela || typeof janela.showModal !== "function") return;

  const form = document.getElementById("form-compra");
  const lista = document.getElementById("compra-itens");
  const vazio = document.getElementById("compra-itens-vazio");
  const alerta = document.getElementById("compra-alerta");
  const corpo = janela.querySelector(".compra-corpo");
  const campoData = document.getElementById("compra-data");
  const caixaNome = document.getElementById("campo-local_nome");
  const campoNome = document.getElementById("compra-local-nome");
  const botaoSalvar = form.querySelector('button[type="submit"]');
  const TEXTO_SALVAR = botaoSalvar.textContent;

  const MENSAGEM_FALHA = "Não foi possível salvar agora. Tente novamente em instantes.";
  const MENSAGEM_SESSAO = "Sua sessão expirou. Entre novamente para registrar a compra.";
  const NOMES = { filamento: "Filamento", acessorio: "Acessório", embalagem: "Embalagem" };

  let sequencia = 0; // ids únicos dos campos de cada linha
  let abridor = null; // botão que abriu a janela (recebe o foco ao fechar)

  // ---------- Chave da submissão ----------
  // UUID v4 novo a cada janela limpa: reenviar a mesma compra (duplo clique, resposta perdida)
  // não a duplica no banco. getRandomValues funciona também fora de HTTPS (acesso pela rede).
  function novaChave() {
    const b = new Uint8Array(16);
    window.crypto.getRandomValues(b);
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    const h = Array.prototype.map.call(b, function (x) { return x.toString(16).padStart(2, "0"); }).join("");
    return [h.slice(0, 8), h.slice(8, 12), h.slice(12, 16), h.slice(16, 20), h.slice(20)].join("-");
  }

  // ---------- Máscaras ----------

  function digitos(texto) {
    return String(texto).replace(/\D/g, "");
  }

  // "01102026" -> "01/10/2026", inserindo as barras enquanto digita (ou ao colar).
  function mascaraData(texto) {
    const d = digitos(texto).slice(0, 8);
    if (d.length <= 2) return d;
    if (d.length <= 4) return d.slice(0, 2) + "/" + d.slice(2);
    return d.slice(0, 2) + "/" + d.slice(2, 4) + "/" + d.slice(4);
  }

  // Centavos (inteiro) -> "R$ 1.234,56".
  function formatarCentavos(centavos) {
    const texto = String(Math.round(centavos)).padStart(3, "0");
    const inteiro = texto.slice(0, -2).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    return "R$ " + inteiro + "," + texto.slice(-2);
  }

  // Estilo bancário: os dígitos entram pela direita ("123456" -> "R$ 1.234,56").
  function mascaraMoeda(texto) {
    const d = digitos(texto).slice(0, 9).replace(/^0+(?=\d)/, "");
    return d.length === 0 ? "" : formatarCentavos(Number(d));
  }

  // Peso por rolo: até 5 dígitos inteiros e 2 decimais, com vírgula ("750,5").
  function mascaraPeso(texto) {
    const t = String(texto).replace(/\./g, ",").replace(/[^\d,]/g, "");
    const virgula = t.indexOf(",");
    if (virgula === -1) return t.slice(0, 5);
    return t.slice(0, virgula).slice(0, 5) + "," + t.slice(virgula + 1).replace(/,/g, "").slice(0, 2);
  }

  const MASCARAS = {
    quantidade: function (t) { return digitos(t); },
    peso_rolo: mascaraPeso,
    valor_unitario: mascaraMoeda,
    valor_total: mascaraMoeda,
  };

  // ---------- Cálculos (prévia) ----------

  function centavos(texto) {
    const d = digitos(texto);
    return d ? Number(d) : null;
  }

  function centigramas(texto) {
    const m = /^(\d+)(?:,(\d{0,2}))?$/.exec(texto);
    if (!m) return null;
    return Number(m[1]) * 100 + Number((m[2] || "").padEnd(2, "0"));
  }

  function gramas(cg) {
    return (cg / 100).toLocaleString("pt-BR", { maximumFractionDigits: 2 }) + " g";
  }

  function valorDe(linha, campo) {
    const el = linha.querySelector('[data-campo="' + campo + '"]');
    return el ? el.value : "";
  }

  function mostrar(linha, calculo, texto) {
    const el = linha.querySelector('[data-calculo="' + calculo + '"]');
    if (el) el.textContent = texto;
  }

  function recalcular(linha) {
    const qtd = Number(digitos(valorDe(linha, "quantidade"))) || 0;
    if (linha.dataset.categoria === "filamento") {
      const peso = centigramas(valorDe(linha, "peso_rolo"));
      const valor = centavos(valorDe(linha, "valor_unitario"));
      mostrar(linha, "peso_total", qtd && peso ? gramas(qtd * peso) : "—");
      mostrar(linha, "valor_total", qtd && valor !== null ? formatarCentavos(qtd * valor) : "—");
      // custo/kg = valor unitário x 1000 / peso por rolo (em centavos e centigramas)
      mostrar(linha, "custo", peso && valor !== null ? formatarCentavos((valor * 100000) / peso) : "—");
    } else {
      const total = centavos(valorDe(linha, "valor_total"));
      mostrar(linha, "custo", qtd && total !== null ? formatarCentavos(total / qtd) : "—");
    }
  }

  // ---------- Linhas ----------

  function linhas() {
    return Array.prototype.slice.call(lista.children);
  }

  function numerar() {
    linhas().forEach(function (linha, i) {
      const nome = NOMES[linha.dataset.categoria];
      linha.querySelector(".compra-item-titulo").textContent = (i + 1) + ". " + nome;
      linha.querySelector(".compra-item-remover").setAttribute("aria-label", "Remover item " + (i + 1) + " (" + nome + ")");
    });
    vazio.hidden = lista.children.length > 0;
  }

  function adicionar(categoria, focar) {
    const modelo = document.getElementById("modelo-" + categoria);
    if (!modelo) return;
    const fragmento = modelo.content.cloneNode(true);
    const linha = fragmento.querySelector(".compra-item");
    const n = ++sequencia;
    linha.querySelectorAll("[data-campo]").forEach(function (el) {
      if (el.type !== "hidden") el.id = "compra-" + n + "-" + el.dataset.campo;
    });
    linha.querySelectorAll("label[data-para]").forEach(function (rotulo) {
      rotulo.htmlFor = "compra-" + n + "-" + rotulo.dataset.para;
    });
    lista.appendChild(fragmento);
    limparErro(document.getElementById("campo-itens"));
    numerar();
    recalcular(linha);
    if (focar) linha.querySelector('[data-campo]:not([type="hidden"])').focus();
  }

  form.querySelectorAll("[data-adicionar]").forEach(function (botao) {
    botao.addEventListener("click", function () { adicionar(botao.dataset.adicionar, true); });
  });

  // Remover uma linha ainda não salva; o foco vai para a linha vizinha (ou para "Adicionar").
  lista.addEventListener("click", function (evento) {
    const botao = evento.target.closest(".compra-item-remover");
    if (!botao) return;
    const linha = botao.closest(".compra-item");
    const vizinha = linha.nextElementSibling || linha.previousElementSibling;
    linha.remove();
    numerar();
    const destino = vizinha ? vizinha.querySelector(".compra-item-remover") : form.querySelector("[data-adicionar]");
    destino.focus();
  });

  // ---------- Local de compra ----------

  // Outro e Loja Física pedem o nome do local; ao trocar para uma plataforma, o texto é limpo.
  function atualizarLocal() {
    const escolhido = form.querySelector('input[name="local"]:checked');
    const comNome = !!escolhido && escolhido.hasAttribute("data-com-nome");
    caixaNome.hidden = !comNome;
    if (!comNome) {
      campoNome.value = "";
      limparErro(caixaNome);
    }
  }

  // ---------- Erros ----------

  function limparErro(caixa) {
    if (!caixa || !caixa.classList.contains("tem-erro")) return;
    caixa.classList.remove("tem-erro");
    caixa.removeAttribute("aria-describedby");
    caixa.querySelectorAll("[aria-invalid]").forEach(function (el) {
      el.removeAttribute("aria-invalid");
      el.removeAttribute("aria-describedby");
    });
    caixa.querySelectorAll(":scope > .campo-erro").forEach(function (msg) { msg.remove(); });
  }

  function limparErros() {
    form.querySelectorAll(".tem-erro").forEach(limparErro);
    alerta.hidden = true;
    alerta.textContent = "";
  }

  let contadorErros = 0;

  // Mensagem no próprio campo, ligada a ele (aria-describedby) para leitores de tela.
  function marcarErro(caixa, alvo, texto) {
    if (!caixa) return null;
    const aviso = document.createElement("p");
    aviso.className = "campo-erro";
    aviso.id = "compra-erro-" + ++contadorErros;
    aviso.textContent = texto;
    caixa.appendChild(aviso);
    caixa.classList.add("tem-erro");
    const descrito = alvo && alvo.type !== "radio" ? alvo : caixa;
    descrito.setAttribute("aria-describedby", aviso.id);
    if (alvo && alvo.type !== "radio") alvo.setAttribute("aria-invalid", "true");
    return alvo;
  }

  // Chave do servidor -> (caixa do campo, elemento que recebe o foco).
  function localizar(chave) {
    const doItem = /^item_(\d+)_([a-z_]+)$/.exec(chave);
    if (doItem) {
      const linha = lista.children[Number(doItem[1])];
      if (!linha) return [null, null];
      if (doItem[2] === "foto") {
        const caixa = linha.querySelector(".compra-item-foto");
        return [caixa, caixa.querySelector(".upload-botao:not([hidden])")];
      }
      const alvo = linha.querySelector('[data-campo="' + doItem[2] + '"]');
      if (!alvo || alvo.type === "hidden") return [linha.querySelector(".compra-item-campos"), linha.querySelector(".compra-item-remover")];
      return [alvo.closest(".campo"), alvo];
    }
    if (chave === "itens") {
      return [document.getElementById("campo-itens"), form.querySelector("[data-adicionar]")];
    }
    if (chave === "local") {
      const caixa = document.getElementById("campo-local");
      return [caixa, caixa.querySelector("input:checked") || caixa.querySelector("input")];
    }
    const alvo = form.querySelector('[name="' + chave + '"]');
    return [alvo && alvo.closest(".campo"), alvo];
  }

  function mostrarAlerta(texto) {
    alerta.textContent = texto;
    alerta.hidden = false;
  }

  function aplicarErros(campos) {
    let primeiro = null;
    Object.keys(campos).forEach(function (chave) {
      const encontrado = localizar(chave);
      const alvo = marcarErro(encontrado[0], encontrado[1], campos[chave]);
      if (!primeiro && alvo) primeiro = alvo;
    });
    if (primeiro) {
      primeiro.focus();
    } else {
      corpo.scrollTop = 0;
    }
  }

  // ---------- Digitação ----------

  form.addEventListener("input", function (evento) {
    const alvo = evento.target;
    if (alvo === campoData) {
      alvo.value = mascaraData(alvo.value);
    } else if (alvo.dataset && MASCARAS[alvo.dataset.campo]) {
      alvo.value = MASCARAS[alvo.dataset.campo](alvo.value);
    }
    limparErro(alvo.closest(".tem-erro"));
    const linha = alvo.closest(".compra-item");
    if (linha) recalcular(linha);
  });

  form.addEventListener("change", function (evento) {
    const alvo = evento.target;
    if (alvo.name === "local") atualizarLocal();
    limparErro(alvo.closest(".tem-erro"));
  });

  // Trocar ou remover a foto apaga o aviso de foto recusada pelo servidor.
  lista.addEventListener("imagemreferencia:alterada", function (evento) {
    limparErro(evento.target.querySelector(".compra-item-foto"));
  });

  // ---------- Abrir e fechar ----------

  function limpar() {
    form.reset();
    lista.replaceChildren(); // libera as miniaturas (imagem-referencia.js observa a lista)
    limparErros();
    atualizarLocal();
    numerar();
    form.elements.chave_envio.value = novaChave();
  }

  function abrir(categoria, botao) {
    abridor = botao || null;
    limpar();
    if (categoria) adicionar(categoria, false);
    janela.showModal();
    corpo.scrollTop = 0;
    campoData.focus();
  }

  function enviando() {
    return form.dataset.envio === "enviando";
  }

  function fechar() {
    if (enviando()) return;
    janela.close();
  }

  document.querySelectorAll("[data-abrir-compra]").forEach(function (botao) {
    botao.addEventListener("click", function () { abrir(botao.dataset.abrirCompra, botao); });
  });

  form.querySelectorAll("[data-cancelar]").forEach(function (botao) {
    botao.addEventListener("click", fechar);
  });

  // Esc: igual a Cancelar, mas não durante o envio.
  janela.addEventListener("cancel", function (evento) {
    if (enviando()) evento.preventDefault();
  });

  janela.addEventListener("close", function () {
    if (form.dataset.envio === "concluido") return; // indo para a lista atualizada
    limpar();
    if (abridor) abridor.focus();
  });

  // ---------- Envio ----------

  function travar(sim) {
    form.dataset.envio = sim ? "enviando" : "";
    form.inert = sim; // nada muda (nem duplo clique) enquanto o servidor responde
    if (sim) form.setAttribute("aria-busy", "true");
    else form.removeAttribute("aria-busy");
    botaoSalvar.textContent = sim ? "Salvando…" : TEXTO_SALVAR;
  }

  function dadosDoFormulario() {
    const dados = new FormData(form); // csrf, chave_envio, data, local e nome do local
    linhas().forEach(function (linha, i) {
      linha.querySelectorAll("[data-campo]").forEach(function (el) {
        dados.append("item_" + i + "_" + el.dataset.campo, el.value);
      });
    });
    const arquivos = window.ImagensReferencia ? window.ImagensReferencia.arquivos() : [];
    arquivos.forEach(function (arquivo, i) {
      if (arquivo) dados.append("item_" + i + "_foto", arquivo, arquivo.name);
    });
    return dados;
  }

  async function enviar() {
    let resposta;
    try {
      resposta = await fetch(form.action, {
        method: "POST",
        body: dadosDoFormulario(),
        credentials: "same-origin",
        redirect: "manual", // sessão expirada vira redirecionamento para /entrar
      });
    } catch (erro) {
      return { erro: MENSAGEM_FALHA };
    }
    if (resposta.type === "opaqueredirect") return { erro: MENSAGEM_SESSAO };
    try {
      const conteudo = await resposta.json();
      return resposta.ok && conteudo.destino ? conteudo : { erro: conteudo.erro || MENSAGEM_FALHA, campos: conteudo.campos };
    } catch (erro) {
      return { erro: MENSAGEM_FALHA };
    }
  }

  form.addEventListener("submit", function (evento) {
    evento.preventDefault();
    if (enviando()) return;
    limparErros();
    travar(true);
    enviar().then(function (resultado) {
      if (resultado.destino) {
        form.dataset.envio = "concluido";
        window.location.assign(resultado.destino);
        return;
      }
      travar(false);
      mostrarAlerta(resultado.erro);
      corpo.scrollTop = 0;
      if (resultado.campos) aplicarErros(resultado.campos);
      else alerta.focus();
    });
  });

  alerta.tabIndex = -1;
})();
