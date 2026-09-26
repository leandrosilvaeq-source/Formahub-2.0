// Tela "Novo pedido": imagem de referência de cada item (opcional: zero ou uma). Somente frontend.
//
// O arquivo escolhido fica apenas na memória do navegador: a miniatura usa uma URL
// temporária (blob:) e nada é enviado, convertido em texto ou guardado no navegador.
// A falta de imagem nunca é um erro e não impede o salvamento do pedido.
// Próximo MVP: enviar `window.ImagensReferencia.arquivos()[n]` ao Supabase Storage e gravar
// no item somente o caminho retornado (o banco nunca guarda os bytes da imagem).
(function () {
  "use strict";

  const lista = document.getElementById("itens");
  if (!lista) return;

  const TIPOS = { "image/png": true, "image/jpeg": true, "image/webp": true };
  const EXTENSOES = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", webp: "image/webp" };

  const controles = new WeakMap(); // linha do item -> controle da imagem

  // 2516582 -> "2,4 MB"
  function formatarTamanho(bytes) {
    if (bytes < 1024) return bytes + " bytes";
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
    return (bytes / (1024 * 1024)).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " MB";
  }

  function formatoAceito(arquivo) {
    if (arquivo.type) return !!TIPOS[arquivo.type];
    const ext = (arquivo.name.split(".").pop() || "").toLowerCase();
    return !!EXTENSOES[ext];
  }

  function temArquivos(evento) {
    const tipos = evento.dataTransfer && evento.dataTransfer.types;
    return !!tipos && Array.prototype.indexOf.call(tipos, "Files") !== -1;
  }

  function criar(linha) {
    const area = linha.querySelector(".upload-item");
    if (!area) return null;

    const limiteMb = Number(area.dataset.maxMb) || 10;
    const limiteBytes = limiteMb * 1024 * 1024;
    const el = {
      dica: area.querySelector(".upload-dica"),
      erro: area.querySelector(".upload-erro"),
      miniatura: area.querySelector(".upload-miniatura"),
      info: area.querySelector(".upload-info"),
      nome: area.querySelector(".upload-nome"),
      tamanho: area.querySelector(".upload-tamanho"),
      ou: area.querySelector(".upload-ou"),
      selecionar: area.querySelector(".upload-selecionar"),
      trocar: area.querySelector(".upload-trocar"),
      remover: area.querySelector(".upload-remover"),
      entrada: area.querySelector(".upload-arquivo"),
      status: area.querySelector(".upload-status"),
    };

    let atual = null; // File válido (ou nenhum)
    let urlAtual = null; // URL temporária da miniatura
    let tentativa = 0; // descarta leituras antigas se o usuário escolher outra rápido
    let mensagem = "";

    function liberarUrl() {
      if (urlAtual) URL.revokeObjectURL(urlAtual);
      urlAtual = null;
    }

    // Mostra só o que faz sentido no estado atual (vazio, com imagem, com erro).
    function desenhar() {
      const tem = !!atual;
      const comErro = mensagem !== "";
      el.dica.hidden = tem || comErro;
      el.ou.hidden = tem;
      el.selecionar.hidden = tem;
      el.miniatura.hidden = !tem;
      el.info.hidden = !tem || comErro;
      el.trocar.hidden = !tem;
      el.remover.hidden = !tem;
      el.erro.hidden = !comErro;
      el.erro.textContent = mensagem;
    }

    function avisar() {
      linha.dispatchEvent(
        new CustomEvent("imagemreferencia:alterada", { bubbles: true, detail: { arquivo: atual } })
      );
    }

    function anunciar(texto) {
      el.status.textContent = "";
      window.setTimeout(function () { el.status.textContent = texto; }, 30);
    }

    function erro(texto) {
      mensagem = texto; // a imagem válida anterior, se houver, continua selecionada
      desenhar();
    }

    function limpar(anunciarRemocao) {
      tentativa += 1;
      liberarUrl();
      atual = null;
      mensagem = "";
      el.entrada.value = "";
      el.miniatura.removeAttribute("src");
      el.nome.textContent = "";
      el.tamanho.textContent = "";
      desenhar();
      if (anunciarRemocao) anunciar("Imagem removida.");
      avisar();
    }

    function validar(arquivos) {
      if (arquivos.length > 1) return "Envie só uma imagem.";
      const arquivo = arquivos[0];
      if (!formatoAceito(arquivo)) return "Formato não aceito.";
      if (arquivo.size > limiteBytes) {
        return "Maior que " + limiteMb + " MB.";
      }
      if (arquivo.size === 0) return "Arquivo vazio.";
      return null;
    }

    function receber(arquivosRecebidos) {
      const arquivos = Array.prototype.slice.call(arquivosRecebidos || []);
      if (arquivos.length === 0) return;
      const problema = validar(arquivos);
      if (problema) {
        erro(problema);
        return;
      }

      const arquivo = arquivos[0];
      const minha = ++tentativa;
      const url = URL.createObjectURL(arquivo);
      const teste = new Image();
      teste.onload = function () {
        if (minha !== tentativa) { URL.revokeObjectURL(url); return; }
        liberarUrl();
        urlAtual = url;
        atual = arquivo;
        mensagem = "";
        el.miniatura.src = url;
        el.nome.textContent = arquivo.name;
        el.nome.title = arquivo.name;
        el.tamanho.textContent = formatarTamanho(arquivo.size);
        desenhar();
        anunciar("Imagem selecionada: " + arquivo.name + ", " + formatarTamanho(arquivo.size) + ".");
        if (area.contains(document.activeElement) || document.activeElement === document.body) el.trocar.focus();
        avisar();
      };
      teste.onerror = function () {
        URL.revokeObjectURL(url);
        if (minha !== tentativa) return;
        erro("Imagem ilegível.");
      };
      teste.src = url;
    }

    function abrirSeletor() {
      el.entrada.click();
    }

    el.selecionar.addEventListener("click", abrirSeletor);
    el.trocar.addEventListener("click", abrirSeletor);
    el.remover.addEventListener("click", function () {
      limpar(true);
      el.selecionar.focus();
    });
    el.entrada.addEventListener("change", function () {
      receber(el.entrada.files);
      el.entrada.value = ""; // permite escolher de novo o mesmo arquivo
    });

    // ---------- Arrastar e soltar (a linha inteira da imagem é a área) ----------
    area.addEventListener("dragenter", function (evento) {
      if (!temArquivos(evento)) return;
      evento.preventDefault();
      area.classList.add("arrastando");
    });
    area.addEventListener("dragover", function (evento) {
      if (!temArquivos(evento)) return;
      evento.preventDefault();
      evento.dataTransfer.dropEffect = "copy";
      area.classList.add("arrastando");
    });
    area.addEventListener("dragleave", function (evento) {
      if (evento.relatedTarget && area.contains(evento.relatedTarget)) return;
      area.classList.remove("arrastando");
    });
    area.addEventListener("drop", function (evento) {
      if (!temArquivos(evento)) return;
      evento.preventDefault();
      area.classList.remove("arrastando");
      receber(evento.dataTransfer.files);
    });

    desenhar();
    return {
      arquivo: function () { return atual; },
      limpar: function () { limpar(false); },
      liberar: liberarUrl,
    };
  }

  // Cada linha de item (existente ou adicionada depois) ganha o seu próprio controle.
  function iniciar(linha) {
    if (linha.nodeType === 1 && linha.classList.contains("item") && !controles.has(linha)) {
      const controle = criar(linha);
      if (controle) controles.set(linha, controle);
    }
  }

  function numerar() {
    Array.prototype.forEach.call(lista.querySelectorAll(".item"), function (linha, i) {
      const area = linha.querySelector(".upload-item");
      if (area) area.setAttribute("aria-label", "Imagem de referência do item " + (i + 1));
    });
  }

  new MutationObserver(function (mudancas) {
    mudancas.forEach(function (mudanca) {
      Array.prototype.forEach.call(mudanca.removedNodes, function (no) {
        const controle = controles.get(no);
        if (controle) controle.liberar(); // item removido: libera a URL temporária
      });
      Array.prototype.forEach.call(mudanca.addedNodes, iniciar);
    });
    numerar();
  }).observe(lista, { childList: true });

  Array.prototype.forEach.call(lista.querySelectorAll(".item"), iniciar);
  numerar();

  // Soltar um arquivo fora de uma área não deve abrir a imagem no lugar da página.
  function foraDeArea(evento) {
    return temArquivos(evento) && !(evento.target.closest && evento.target.closest(".upload-item"));
  }
  window.addEventListener("dragover", function (evento) { if (foraDeArea(evento)) evento.preventDefault(); });
  window.addEventListener("drop", function (evento) { if (foraDeArea(evento)) evento.preventDefault(); });

  function todos() {
    return Array.prototype.map.call(lista.querySelectorAll(".item"), function (linha) {
      return controles.get(linha) || null;
    });
  }

  const form = document.getElementById("form-pedido");
  if (form) {
    form.addEventListener("reset", function () {
      todos().forEach(function (c) { if (c) c.limpar(); });
    });
  }
  window.addEventListener("pagehide", function () {
    todos().forEach(function (c) { if (c) c.liberar(); });
  });

  window.ImagensReferencia = {
    // Uma posição por item, na ordem da tela: o File escolhido ou null (item sem imagem).
    arquivos: function () { return todos().map(function (c) { return c ? c.arquivo() : null; }); },
    limpar: function () { todos().forEach(function (c) { if (c) c.limpar(); }); },
  };
})();
