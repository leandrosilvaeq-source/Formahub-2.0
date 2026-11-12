// Tela Compras: janela Registrar Compra (abrir, lista única de itens, máscaras, cálculos e envio).
//
// Cada linha tem uma categoria e mostra só os campos dela, na ordem de data-campos (vinda do
// servidor). Trocar a categoria não apaga nada: os campos que deixam de aparecer guardam o que
// foi digitado (e voltam se a categoria voltar), mas não são enviados; a linha avisa e oferece
// "Desfazer troca".
// Os cálculos da tela são só uma prévia (valores inteiros em centavos, para não acumular erro):
// subtotal = soma de quantidade x valor unitário; total = subtotal − desconto + frete. O servidor
// valida e calcula tudo de novo com Decimal.
// Envio: multipart/form-data com cada campo visível da linha na posição i como item_{i}_{campo} e
// a foto como item_{i}_foto. A resposta é JSON: {destino} no sucesso; {erro, campos} quando não
// salva (a janela continua aberta com tudo o que foi digitado).
// Cancelar (botão, × ou Esc) fecha a janela sem enviar nada e descarta o que foi digitado.
// Editar (botão de cada compra da lista, data-editar-compra com os dados em JSON) abre a mesma
// janela já preenchida, com o título "Editar Compra", e envia para /compras/{id}/editar.
(function () {
  "use strict";

  const janela = document.getElementById("compra-janela");
  if (!janela || typeof janela.showModal !== "function") return;

  const form = document.getElementById("form-compra");
  const lista = document.getElementById("compra-itens");
  const vazio = form.querySelector(".compra-itens-vazio");
  const botaoAdicionar = form.querySelector("[data-adicionar]");
  const alerta = document.getElementById("compra-alerta");
  const corpo = janela.querySelector(".compra-corpo");
  const campoData = document.getElementById("compra-data");
  const caixaNome = document.getElementById("campo-local_nome");
  const campoNome = document.getElementById("compra-local-nome");
  const campoFrete = document.getElementById("compra-frete");
  const campoDesconto = document.getElementById("compra-desconto");
  const avisoDesconto = document.getElementById("compra-desconto-aviso");
  const saidaSubtotal = document.getElementById("compra-subtotal");
  const saidaTotal = document.getElementById("compra-total");
  const botaoSalvar = form.querySelector('button[type="submit"]');
  const titulo = document.getElementById("compra-titulo");
  const NOVA = { acao: form.getAttribute("action"), titulo: titulo.textContent, salvar: botaoSalvar.textContent };
  const EDICAO = { titulo: "Editar Compra", salvar: "Salvar alterações" };
  let textoSalvar = NOVA.salvar;

  const CAMPOS = JSON.parse(form.dataset.campos); // categoria -> campos, na ordem da linha
  const OPCIONAIS = JSON.parse(form.dataset.opcionais); // opcionais fora do filamento
  const NOMES_CATEGORIAS = JSON.parse(form.dataset.categorias);
  // Categorias com a opção Kit: valor do kit informado, valor unitário = kit ÷ quantidade.
  const CATEGORIAS_KIT = JSON.parse(form.dataset.categoriasKit || "[]");
  const LIMITE_ITENS = Number(form.dataset.limiteItens) || 50;

  const MENSAGEM_FALHA = "Não foi possível salvar agora. Tente novamente em instantes.";
  const MENSAGEM_SESSAO = "Sua sessão expirou. Entre novamente para registrar a compra.";

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

  // Centavos (inteiro) -> "R$ 1.234,56" (negativo: "−R$ 1,00").
  function formatarCentavos(centavos) {
    const sinal = centavos < 0 ? "−" : "";
    const texto = String(Math.round(Math.abs(centavos))).padStart(3, "0");
    const inteiro = texto.slice(0, -2).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    return sinal + "R$ " + inteiro + "," + texto.slice(-2);
  }

  // Estilo bancário: os dígitos entram pela direita ("123456" -> "R$ 1.234,56").
  function mascaraMoeda(texto) {
    const d = digitos(texto).slice(0, 9).replace(/^0+(?=\d)/, "");
    return d.length === 0 ? "" : formatarCentavos(Number(d));
  }

  // Data: só números, com as barras no lugar ("10092026" -> "10/09/2026").
  function mascaraData(texto) {
    const d = digitos(texto).slice(0, 8);
    if (d.length <= 2) return d;
    if (d.length <= 4) return d.slice(0, 2) + "/" + d.slice(2);
    return d.slice(0, 2) + "/" + d.slice(2, 4) + "/" + d.slice(4);
  }

  const MASCARAS = {
    quantidade: function (t) { return digitos(t); },
    valor_unitario: mascaraMoeda,
    valor_kit: mascaraMoeda,
  };

  // ---------- Cálculos (prévia) ----------

  function centavos(texto) {
    const d = digitos(texto);
    return d ? Number(d) : 0;
  }

  function campoDe(linha, nome) {
    return linha.querySelector('[data-campo="' + nome + '"]');
  }

  function valorDe(linha, nome) {
    const el = campoDe(linha, nome);
    return el ? el.value : "";
  }

  function quantidadeDe(linha) {
    return Number(digitos(valorDe(linha, "quantidade"))) || 0;
  }

  // Linha marcada como kit, numa categoria que aceita kit.
  function kitAtivo(linha) {
    const caixa = campoDe(linha, "kit");
    return CATEGORIAS_KIT.indexOf(linha.dataset.categoria) !== -1 && !!caixa && caixa.checked;
  }

  // Valor da linha = quantidade x valor unitário; no kit, o valor do kit (linhas incompletas
  // contam o que já têm).
  function valorDaLinha(linha) {
    if (kitAtivo(linha)) return centavos(valorDe(linha, "valor_kit"));
    return quantidadeDe(linha) * centavos(valorDe(linha, "valor_unitario"));
  }

  // Kit: valor unitário = valor do kit ÷ quantidade, arredondado ao centavo, mostrado abaixo do
  // total (o servidor grava o valor do kit; o custo unitário do lote sai da divisão exata). O
  // campo Valor unit. (oculto no kit) recebe o mesmo valor, para ficar pronto se o kit for
  // desmarcado.
  function atualizarUnitarioDoKit(linha) {
    const quantidade = quantidadeDe(linha);
    const kit = centavos(valorDe(linha, "valor_kit"));
    const unitario = quantidade && kit ? formatarCentavos(Math.round(kit / quantidade)) : "";
    campoDe(linha, "valor_unitario").value = unitario;
    linha.querySelector("[data-unitario-kit]").textContent = (unitario || "R$ 0,00") + " / un.";
  }

  function atualizarTotais() {
    let subtotal = 0;
    linhas().forEach(function (linha) {
      if (kitAtivo(linha)) atualizarUnitarioDoKit(linha);
      const valor = valorDaLinha(linha);
      linha.querySelector("[data-total-linha]").textContent = formatarCentavos(valor);
      subtotal += valor;
    });
    const desconto = centavos(campoDesconto.value);
    const frete = centavos(campoFrete.value);
    saidaSubtotal.textContent = formatarCentavos(subtotal);
    saidaTotal.textContent = formatarCentavos(subtotal - desconto + frete);
    avisoDesconto.hidden = desconto <= subtotal;
  }

  // ---------- Linhas ----------

  function linhas() {
    // Na ordem da tela: é a posição enviada ao servidor.
    return Array.prototype.slice.call(lista.querySelectorAll(".compra-item"));
  }

  // Campos enviados pela linha: os da categoria e, no kit, o valor do kit.
  function camposDaLinha(linha) {
    const campos = (CAMPOS[linha.dataset.categoria] || []).slice();
    if (kitAtivo(linha)) campos.push("kit", "valor_kit");
    return campos;
  }

  // Campos de outras categorias que guardam algo digitado (ficariam de fora ao salvar).
  function guardados(linha) {
    const visiveis = camposDaLinha(linha);
    return Array.prototype.filter.call(linha.querySelectorAll("[data-campo-de]"), function (caixa) {
      const nome = caixa.dataset.campoDe;
      const el = campoDe(linha, nome);
      return visiveis.indexOf(nome) === -1 && el && el.value.trim() !== "";
    }).map(function (caixa) {
      return caixa.querySelector("label").firstChild.textContent.trim();
    });
  }

  function atualizarAviso(linha) {
    const aviso = linha.querySelector(".compra-item-aviso");
    const nomes = guardados(linha);
    aviso.hidden = nomes.length === 0 || !linha.dataset.anterior;
    if (!aviso.hidden) {
      aviso.querySelector(".compra-item-aviso-texto").textContent =
        "Não usados em " + NOMES_CATEGORIAS[linha.dataset.categoria] + " e não serão salvos: " +
        nomes.join(", ") + ".";
    }
  }

  // Mostra só os campos da categoria, na ordem dela; os outros ficam ocultos com o que já têm.
  function aplicarCategoria(linha, categoria) {
    const visiveis = CAMPOS[categoria] || [];
    const caixa = linha.querySelector(".compra-item-campos");
    linha.dataset.categoria = categoria;
    campoDe(linha, "categoria").value = categoria;
    linha.querySelectorAll("[data-campo-de]").forEach(function (campo) {
      const nome = campo.dataset.campoDe;
      campo.hidden = visiveis.indexOf(nome) === -1;
      const opcional = categoria !== "filamento" && OPCIONAIS.indexOf(nome) !== -1;
      campo.querySelector(".obrigatorio").hidden = opcional;
      campo.classList.toggle("compra-campo-opcional", opcional);
    });
    visiveis.forEach(function (nome) {
      caixa.insertBefore(linha.querySelector('[data-campo-de="' + nome + '"]'), caixa.querySelector(".compra-item-total"));
    });
    const nome = campoDe(linha, "nome");
    if (categoria === "filamento") nome.removeAttribute("list");
    else nome.setAttribute("list", "compra-nomes-" + categoria);
    aplicarKit(linha);
    // Erro de um campo que sumiu deixa de valer.
    linha.querySelectorAll(".compra-campo[hidden].tem-erro").forEach(limparErro);
    atualizarAviso(linha);
  }

  // Opção Kit: aparece só nas categorias de kit, logo antes do valor. Marcada, troca o campo
  // Valor unit. pelo Valor do kit, e o valor unitário calculado aparece abaixo do total.
  function aplicarKit(linha) {
    const caixas = linha.querySelector(".compra-item-campos");
    const caixaKit = linha.querySelector("[data-kit]");
    const caixaValorKit = linha.querySelector('[data-campo-de="valor_kit"]');
    const caixaUnitario = linha.querySelector('[data-campo-de="valor_unitario"]');
    const ativo = kitAtivo(linha);
    caixaKit.hidden = CATEGORIAS_KIT.indexOf(linha.dataset.categoria) === -1;
    caixas.insertBefore(caixaKit, caixaUnitario);
    caixas.insertBefore(caixaValorKit, caixaUnitario);
    caixaValorKit.hidden = !ativo;
    caixaUnitario.hidden = ativo;
    linha.querySelector("[data-unitario-kit]").hidden = !ativo;
    limparErro(ativo ? caixaUnitario : caixaValorKit);
  }

  // Cada linha é numerada na lista (aria-label e botões); data-pos é a posição na tela.
  function numerar() {
    linhas().forEach(function (linha, i) {
      const n = i + 1;
      linha.dataset.pos = String(n);
      linha.setAttribute("aria-label", "Item " + n);
      const remover = linha.querySelector(".compra-item-remover");
      remover.setAttribute("aria-label", "Remover item " + n);
      remover.title = "Remover item " + n;
      const duplicar = linha.querySelector(".compra-item-duplicar");
      duplicar.setAttribute("aria-label", "Duplicar item " + n);
      duplicar.title = "Duplicar item " + n;
    });
    const total = linhas().length;
    vazio.hidden = total > 0;
    botaoAdicionar.disabled = total >= LIMITE_ITENS;
    atualizarTotais();
  }

  // Nova linha (no fim da lista ou logo depois de `depois`), na categoria pedida.
  function adicionar(categoria, focar, depois) {
    const modelo = document.getElementById("modelo-item");
    const fragmento = modelo.content.cloneNode(true);
    const linha = fragmento.querySelector(".compra-item");
    const n = ++sequencia;
    linha.querySelectorAll("[data-campo]").forEach(function (el) {
      if (el.type !== "hidden") el.id = "compra-" + n + "-" + el.dataset.campo;
    });
    linha.querySelectorAll("label[data-para]").forEach(function (rotulo) {
      rotulo.htmlFor = "compra-" + n + "-" + rotulo.dataset.para;
    });
    aplicarCategoria(linha, categoria || "filamento");
    if (depois) depois.after(fragmento);
    else lista.appendChild(fragmento);
    limparErro(document.getElementById("campo-itens"));
    numerar();
    if (focar) campoDe(linha, "categoria").focus();
    return linha;
  }

  botaoAdicionar.addEventListener("click", function () {
    const ultima = linhas().pop();
    adicionar(ultima ? ultima.dataset.categoria : "filamento", true);
  });

  // Duplicar: nova linha logo abaixo, com os mesmos dados (a foto não é copiada: cada linha
  // tem a sua).
  function duplicar(linha) {
    const copia = adicionar(linha.dataset.categoria, false, linha);
    linha.querySelectorAll("[data-campo]").forEach(function (el) {
      const nome = el.dataset.campo;
      if (nome === "categoria" || nome === "foto_atual") return;
      if (el.type === "checkbox") campoDe(copia, nome).checked = el.checked;
      else campoDe(copia, nome).value = el.value;
    });
    aplicarKit(copia);
    atualizarTotais();
    campoDe(copia, "categoria").focus();
  }

  lista.addEventListener("click", function (evento) {
    const linha = evento.target.closest(".compra-item");
    if (!linha) return;
    if (evento.target.closest(".compra-item-duplicar")) {
      duplicar(linha);
    } else if (evento.target.closest(".compra-item-remover")) {
      // O foco vai para a vizinha (ou para "+ Adicionar item").
      const vizinha = linha.nextElementSibling || linha.previousElementSibling;
      linha.remove();
      numerar();
      (vizinha ? vizinha.querySelector(".compra-item-remover") : botaoAdicionar).focus();
    } else if (evento.target.closest(".compra-item-desfazer")) {
      const anterior = linha.dataset.anterior;
      delete linha.dataset.anterior;
      aplicarCategoria(linha, anterior);
      atualizarTotais();
      campoDe(linha, "categoria").focus();
    }
  });

  // ---------- Local de compra ----------

  // Loja Física e Outros sites pedem o nome; ao trocar para uma plataforma, o texto é limpo.
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
      const linha = linhas()[Number(doItem[1])];
      if (!linha) return [null, null];
      if (doItem[2] === "foto") {
        const caixa = linha.querySelector(".compra-item-foto");
        return [caixa, caixa.querySelector(".upload-botao:not([hidden])")];
      }
      const alvo = campoDe(linha, doItem[2]);
      const caixa = alvo && alvo.closest(".campo");
      if (!caixa || caixa.hidden || alvo.type === "hidden") {
        return [linha.querySelector(".compra-item-campos"), campoDe(linha, "categoria")];
      }
      return [caixa, alvo];
    }
    if (chave === "itens") return [document.getElementById("campo-itens"), botaoAdicionar];
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
    if (primeiro) primeiro.focus();
    else corpo.scrollTop = 0;
  }

  // ---------- Digitação ----------

  form.addEventListener("input", function (evento) {
    const alvo = evento.target;
    if (alvo === campoFrete || alvo === campoDesconto) {
      alvo.value = mascaraMoeda(alvo.value);
      atualizarTotais();
    } else if (alvo === campoData) {
      alvo.value = mascaraData(alvo.value);
    } else if (alvo.tagName !== "SELECT" && alvo.dataset && MASCARAS[alvo.dataset.campo]) {
      alvo.value = MASCARAS[alvo.dataset.campo](alvo.value);
    }
    limparErro(alvo.closest(".tem-erro"));
    const linha = alvo.closest(".compra-item");
    if (linha) {
      atualizarTotais();
      if (!linha.querySelector(".compra-item-aviso").hidden) atualizarAviso(linha);
    }
  });

  form.addEventListener("change", function (evento) {
    const alvo = evento.target;
    if (alvo.name === "local") atualizarLocal();
    if (alvo.dataset && alvo.dataset.campo === "kit") {
      const linha = alvo.closest(".compra-item");
      aplicarKit(linha);
      atualizarTotais();
      if (alvo.checked) campoDe(linha, "valor_kit").focus();
    }
    if (alvo.dataset && alvo.dataset.campo === "categoria") {
      const linha = alvo.closest(".compra-item");
      if (alvo.value !== linha.dataset.categoria) {
        linha.dataset.anterior = linha.dataset.categoria;
        aplicarCategoria(linha, alvo.value);
        limparErro(linha.querySelector(".compra-item-campos"));
        atualizarTotais();
      }
    }
    limparErro(alvo.closest(".tem-erro"));
  });

  // Trocar ou remover a foto apaga o aviso de foto recusada pelo servidor e, na edição, deixa
  // de manter a foto já gravada.
  lista.addEventListener("imagemreferencia:alterada", function (evento) {
    limparErro(evento.target.querySelector(".compra-item-foto"));
    const gravada = evento.target.querySelector('[data-campo="foto_atual"]');
    if (gravada) gravada.value = "";
  });

  // ---------- Abrir e fechar ----------

  // Modo da janela: compra nova (padrão) ou edição da compra `id`.
  function definirModo(id) {
    const modo = id ? EDICAO : NOVA;
    form.setAttribute("action", id ? "/compras/" + id + "/editar" : NOVA.acao);
    titulo.textContent = modo.titulo;
    textoSalvar = modo.salvar;
    botaoSalvar.textContent = textoSalvar;
  }

  function limpar() {
    definirModo(null);
    form.reset();
    lista.replaceChildren(); // libera as miniaturas (imagem-referencia.js observa a lista)
    limparErros();
    atualizarLocal();
    numerar();
    form.elements.chave_envio.value = novaChave();
    campoData.value = hoje();
  }

  // Data de hoje (fuso local) em dd/mm/aaaa.
  function hoje() {
    const agora = new Date();
    const dia = String(agora.getDate()).padStart(2, "0");
    const mes = String(agora.getMonth() + 1).padStart(2, "0");
    return dia + "/" + mes + "/" + agora.getFullYear();
  }

  function mostrar() {
    janela.showModal();
    corpo.scrollTop = 0;
    campoData.focus();
  }

  function abrir(botao) {
    abridor = botao || null;
    limpar();
    adicionar("filamento", false);
    mostrar();
  }

  // "99.90" (reais, do servidor) -> "R$ 99,90"; zero ou vazio -> "".
  function moedaDoServidor(texto) {
    const c = digitos(Number(texto || 0).toFixed(2));
    return Number(c) ? formatarCentavos(Number(c)) : "";
  }

  // Abre a janela com a compra `dados` (JSON do botão Editar da lista) já preenchida.
  function abrirEdicao(dados, botao) {
    abridor = botao || null;
    limpar();
    definirModo(dados.id);
    campoData.value = dados.data;
    const local = form.querySelector('input[name="local"][value="' + dados.local + '"]');
    if (local) local.checked = true;
    atualizarLocal();
    campoNome.value = dados.local_nome || "";
    campoFrete.value = moedaDoServidor(dados.frete);
    campoDesconto.value = moedaDoServidor(dados.desconto);
    dados.itens.forEach(function (item) {
      const linha = adicionar(item.categoria, false);
      Object.keys(item).forEach(function (campo) {
        const el = campoDe(linha, campo);
        if (!el || campo === "categoria") return;
        if (el.type === "checkbox") el.checked = item[campo] === "1";
        else if (campo === "valor_unitario" || campo === "valor_kit") el.value = moedaDoServidor(item[campo]);
        else el.value = item[campo];
      });
      aplicarKit(linha);
      if (item.nota) {
        const nota = linha.querySelector(".compra-item-nota");
        nota.textContent = item.nota;
        nota.hidden = false;
      }
      if (item.foto_atual && window.ImagensReferencia) {
        window.ImagensReferencia.mostrarGravada(linha, item.foto_url, "Foto atual");
      }
    });
    atualizarTotais();
    mostrar();
  }

  document.querySelectorAll("[data-editar-compra]").forEach(function (botao) {
    botao.addEventListener("click", function () {
      abrirEdicao(JSON.parse(botao.dataset.editarCompra), botao);
    });
  });

  function enviando() {
    return form.dataset.envio === "enviando";
  }

  function fechar() {
    if (enviando()) return;
    janela.close();
  }

  document.querySelectorAll("[data-abrir-compra]").forEach(function (botao) {
    botao.addEventListener("click", function () { abrir(botao); });
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
    botaoSalvar.textContent = sim ? "Salvando…" : textoSalvar;
  }

  function dadosDoFormulario() {
    // csrf, chave_envio, data, local, nome do local, desconto e frete
    const dados = new FormData(form);
    linhas().forEach(function (linha, i) {
      const enviar = ["categoria", "foto_atual"].concat(camposDaLinha(linha));
      enviar.forEach(function (nome) {
        dados.append("item_" + i + "_" + nome, valorDe(linha, nome)); // kit marcado: "1"
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
