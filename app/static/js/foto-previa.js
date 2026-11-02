// Prévia ampliada da foto (a mesma imagem privada, com a URL assinada que a página já tem).
// Usada pela Lista de Pedidos e pela tela Compras: `FotoPrevia.iniciar(raiz, opcoes)` atende os
// botões .foto-previa-botao (com um <img> dentro) que estiverem dentro de `raiz`.
//
// Mouse: abre após 2 s sobre a foto e fecha ao sair da foto e da prévia. Teclado: abre após 2 s
// com a foto focada e fecha ao sair do foco. Clique, Enter, Espaço ou toque: abre na hora e fecha
// ao tocar fora, no botão Fechar ou com Esc. Esc fecha em todos os casos.
//
// opcoes.bloqueada: função que devolve true quando outra janela (ex.: filtro) está aberta e o
//   Esc não deve fechar a prévia.
// opcoes.lupa: true acrescenta a lupa. Com o mouse sobre a imagem ela amplia os detalhes sob o
//   cursor; o botão Lupa (teclado e toque) fixa a ampliação, que então segue o dedo ou o cursor
//   e as setas do teclado. Nada fica guardado no navegador.
(function () {
  "use strict";

  const ATRASO_PREVIA = 2000;
  const MARGEM = 8;
  const ZOOM = 2.5;
  const PASSO = 12; // % do deslocamento do foco a cada seta
  const ICONE_FECHAR = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M6 6l12 12M18 6 6 18"/></svg>';
  const ICONE_LUPA = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="10.5" cy="10.5" r="6"/><path d="m15 15 5 5M10.5 8v5M8 10.5h5"/></svg>';

  function iniciar(raiz, opcoes) {
    const config = opcoes || {};
    const bloqueada = config.bloqueada || function () { return false; };
    let previa = null;
    let origem = null;
    let modo = "";
    let temporizador = null;

    function cancelarAgendamento() {
      if (temporizador !== null) window.clearTimeout(temporizador);
      temporizador = null;
    }

    function fechar(devolverFoco) {
      cancelarAgendamento();
      if (!previa) return;
      previa.remove();
      previa = null;
      if (origem) {
        origem.setAttribute("aria-expanded", "false");
        if (devolverFoco) origem.focus();
      }
      origem = null;
      modo = "";
    }

    // Ao lado da foto (direita, depois esquerda); sem espaço (celular), centralizada. Nunca
    // passa das bordas da tela.
    function posicionar(botao) {
      const lado = Math.min(420, window.innerWidth - 2 * MARGEM, window.innerHeight - 2 * MARGEM);
      const r = botao.getBoundingClientRect();
      let esquerda;
      let topo = r.top + r.height / 2 - lado / 2;
      if (r.right + lado + MARGEM <= window.innerWidth) {
        esquerda = r.right;
      } else if (r.left - lado >= MARGEM) {
        esquerda = r.left - lado;
      } else {
        esquerda = (window.innerWidth - lado) / 2;
        topo = (window.innerHeight - lado) / 2;
      }
      previa.style.width = lado + "px";
      previa.style.height = lado + "px";
      previa.style.left = Math.round(Math.max(MARGEM, Math.min(esquerda, window.innerWidth - lado - MARGEM))) + "px";
      previa.style.top = Math.round(Math.max(MARGEM, Math.min(topo, window.innerHeight - lado - MARGEM))) + "px";
    }

    // Lupa: o quadro recorta a imagem ampliada; o foco (em %) é o ponto da foto sob o cursor.
    function montarLupa(quadro, botaoLupa) {
      let fixa = false;
      let foco = { x: 50, y: 50 };

      function desenhar(ligada) {
        quadro.style.setProperty("--lupa-x", foco.x + "%");
        quadro.style.setProperty("--lupa-y", foco.y + "%");
        quadro.classList.toggle("ampliado", ligada);
        quadro.classList.toggle("fixa", fixa);
      }
      function focar(evento) {
        const r = quadro.getBoundingClientRect();
        foco = {
          x: Math.max(0, Math.min(100, ((evento.clientX - r.left) / r.width) * 100)),
          y: Math.max(0, Math.min(100, ((evento.clientY - r.top) / r.height) * 100)),
        };
      }

      quadro.addEventListener("pointerenter", function (evento) {
        if (evento.pointerType !== "mouse") return;
        focar(evento);
        desenhar(true);
      });
      quadro.addEventListener("pointerleave", function (evento) {
        if (evento.pointerType === "mouse" && !fixa) desenhar(false);
      });
      quadro.addEventListener("pointerdown", function (evento) {
        if (!fixa || evento.pointerType === "mouse") return;
        focar(evento);
        desenhar(true);
      });
      quadro.addEventListener("pointermove", function (evento) {
        if (evento.pointerType !== "mouse" && !fixa) return;
        focar(evento);
        desenhar(true);
      });
      botaoLupa.addEventListener("click", function () {
        fixa = !fixa;
        botaoLupa.setAttribute("aria-pressed", fixa ? "true" : "false");
        botaoLupa.title = fixa ? "Desligar a lupa" : "Ampliar detalhes";
        desenhar(fixa);
      });
      previa.addEventListener("keydown", function (evento) {
        if (!fixa) return;
        const setas = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
        const seta = setas[evento.key];
        if (!seta) return;
        evento.preventDefault();
        foco = {
          x: Math.max(0, Math.min(100, foco.x + seta[0] * PASSO)),
          y: Math.max(0, Math.min(100, foco.y + seta[1] * PASSO)),
        };
        desenhar(true);
      });
    }

    function abrir(botao, novoModo) {
      cancelarAgendamento();
      const miniatura = botao.querySelector("img");
      if (!miniatura) return;
      if (previa && origem === botao) {
        if (novoModo === "clique" && modo !== "clique") { // já aberta por hover/foco: vira fixa
          modo = "clique";
          previa.querySelector(".foto-previa-fechar").classList.add("visivel");
        }
        return;
      }
      fechar(false);
      previa = document.createElement("div");
      previa.className = "foto-previa";
      previa.setAttribute("role", "dialog");
      previa.setAttribute("aria-label", miniatura.alt);
      const imagem = document.createElement("img");
      imagem.src = miniatura.currentSrc || miniatura.src;
      imagem.alt = miniatura.alt;
      imagem.referrerPolicy = "no-referrer";
      const fechamento = document.createElement("button");
      fechamento.type = "button";
      fechamento.className = "foto-previa-fechar" + (novoModo === "clique" ? " visivel" : "");
      fechamento.title = "Fechar prévia";
      fechamento.setAttribute("aria-label", "Fechar prévia da foto");
      fechamento.innerHTML = ICONE_FECHAR;
      fechamento.addEventListener("click", function () { fechar(true); });
      if (config.lupa) {
        const quadro = document.createElement("div");
        quadro.className = "foto-previa-quadro";
        quadro.style.setProperty("--lupa-zoom", String(ZOOM));
        quadro.append(imagem);
        const botaoLupa = document.createElement("button");
        botaoLupa.type = "button";
        botaoLupa.className = "foto-previa-lupa";
        botaoLupa.title = "Ampliar detalhes";
        botaoLupa.setAttribute("aria-label", "Lupa: ampliar detalhes da foto (setas movem a área ampliada)");
        botaoLupa.setAttribute("aria-pressed", "false");
        botaoLupa.innerHTML = ICONE_LUPA;
        previa.append(quadro, fechamento, botaoLupa);
        document.body.append(previa);
        montarLupa(quadro, botaoLupa);
      } else {
        previa.append(imagem, fechamento);
        document.body.append(previa);
      }
      origem = botao;
      modo = novoModo;
      botao.setAttribute("aria-expanded", "true");
      posicionar(botao);
      if (novoModo === "clique") fechamento.focus();
    }

    function agendar(botao, novoModo) {
      cancelarAgendamento();
      if (previa && origem === botao) return;
      temporizador = window.setTimeout(function () {
        temporizador = null;
        abrir(botao, novoModo);
      }, ATRASO_PREVIA);
    }

    function dentroDe(elemento, alvo) { return !!alvo && elemento.contains(alvo); }

    raiz.addEventListener("pointerover", function (evento) {
      if (evento.pointerType !== "mouse") return;
      const botao = evento.target.closest(".foto-previa-botao");
      if (botao && !dentroDe(botao, evento.relatedTarget)) agendar(botao, "mouse");
    });
    raiz.addEventListener("pointerout", function (evento) {
      if (evento.pointerType !== "mouse") return;
      const botao = evento.target.closest(".foto-previa-botao");
      if (!botao || dentroDe(botao, evento.relatedTarget)) return;
      cancelarAgendamento();
      if (previa && modo === "mouse" && !dentroDe(previa, evento.relatedTarget)) fechar(false);
    });
    document.addEventListener("pointerout", function (evento) {
      if (!previa || modo !== "mouse" || evento.pointerType !== "mouse") return;
      if (!previa.contains(evento.target) || dentroDe(previa, evento.relatedTarget)) return;
      if (!dentroDe(origem, evento.relatedTarget)) fechar(false);
    });

    raiz.addEventListener("focusin", function (evento) {
      const botao = evento.target.closest(".foto-previa-botao");
      if (botao && botao.matches(":focus-visible")) agendar(botao, "foco");
    });
    raiz.addEventListener("focusout", function (evento) {
      const botao = evento.target.closest(".foto-previa-botao");
      if (!botao) return;
      cancelarAgendamento();
      if (previa && modo === "foco" && !dentroDe(previa, evento.relatedTarget)) fechar(false);
    });

    raiz.addEventListener("click", function (evento) {
      const botao = evento.target.closest(".foto-previa-botao");
      if (botao) abrir(botao, "clique");
    });
    document.addEventListener("pointerdown", function (evento) {
      if (!previa || modo !== "clique") return;
      if (previa.contains(evento.target) || origem.contains(evento.target)) return;
      fechar(false);
    });
    document.addEventListener("keydown", function (evento) {
      if (evento.key === "Escape" && previa && !bloqueada()) fechar(true);
    });
    window.addEventListener("resize", function () { fechar(false); });
    window.addEventListener("scroll", function () { fechar(false); }, true);

    // Foto que não carregou (URL expirada): o botão some, sem deixar espaço vazio.
    raiz.addEventListener("error", function (evento) {
      const botao = evento.target.closest && evento.target.closest(".foto-previa-botao");
      if (botao && evento.target.tagName === "IMG") botao.remove();
    }, true);
  }

  window.FotoPrevia = { iniciar: iniciar };
})();
