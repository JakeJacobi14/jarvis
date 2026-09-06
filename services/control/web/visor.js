(() => {
  const MOUTHS = {
    idle: "M86 110 Q100 116 114 110",
    smile: "M84 108 Q100 120 116 108",
    listen: "M88 112 Q100 116 112 112",
    think: "M90 114 Q102 112 112 116",
  };

  let mouth = null;

  function setMouth(name) {
    if (mouth) mouth.setAttribute("d", MOUTHS[name] || MOUTHS.idle);
  }

  function apply(next) {
    const state = next || "idle";
    if (state === "listening") setMouth("listen");
    else if (state === "thinking") setMouth("think");
    else if (state === "speaking") setMouth("smile");
    else setMouth("idle");
  }

  function init() {
    mouth = document.querySelector(".visor .mouth");
    if (!mouth) return;
    setMouth("idle");
    window.JarvisVisor = { setState: apply };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
