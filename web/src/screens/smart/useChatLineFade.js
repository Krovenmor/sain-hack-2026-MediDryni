import { useLayoutEffect, useRef } from "react";

export function useChatLineFade(log, turns, showPhrases) {
  const lastView = useRef(null);
  useLayoutEffect(() => {
    const viewport = log.current;
    if (!viewport) return;
    let frame = 0;
    let disposed = false;

    function update() {
      frame = 0;
      const { top, bottom, height } = viewport.getBoundingClientRect();
      if (!height) return;
      for (const block of viewport.querySelectorAll(".guest-text, .room-message p")) {
        const rect = block.getBoundingClientRect();
        if (rect.bottom <= top || rect.top >= bottom) continue;
        const maskTarget = block.closest(".guest-message") ?? block;
        const textOffset = maskTarget === block ? 0 : rect.top - maskTarget.getBoundingClientRect().top;
        const lineHeight = parseFloat(getComputedStyle(block).lineHeight);
        const first = Math.max(0, Math.floor((top - rect.top) / lineHeight));
        const end = Math.min(Math.ceil(rect.height / lineHeight), Math.ceil((bottom - rect.top) / lineHeight));
        const stops = [];
        for (let line = first; line < end; line++) {
          const center = rect.top + (line + 0.5) * lineHeight;
          const depth = Math.max(0, Math.min(1, (center - top) / (height * 0.8)));
          const alpha = 0.07 + 0.93 * depth;
          const start = textOffset + line * lineHeight;
          stops.push(`rgb(0 0 0 / ${alpha.toFixed(3)}) ${start}px ${start + lineHeight}px`);
        }
        maskTarget.style.maskImage = stops.length ? `linear-gradient(to bottom, ${stops.join(", ")})` : "";
      }
    }

    function schedule() {
      if (!disposed && !frame) frame = requestAnimationFrame(update);
    }

    const first = turns[0]?.id;
    const tail = turns.at(-1)?.id;
    const previous = lastView.current;
    const prepended = previous?.viewport === viewport && first !== previous.first && tail === previous.tail;
    viewport.scrollTop = prepended ? previous.top + viewport.scrollHeight - previous.height : viewport.scrollHeight;
    update();
    let following = !prepended;
    function remember() {
      lastView.current = { viewport, first, tail, top: viewport.scrollTop, height: viewport.scrollHeight };
    }
    remember();
    function onScroll() {
      following = viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop < 4;
      remember();
      schedule();
    }
    const observer = new ResizeObserver(() => {
      if (following) viewport.scrollTop = viewport.scrollHeight;
      remember();
      schedule();
    });
    observer.observe(viewport);
    if (viewport.lastElementChild) observer.observe(viewport.lastElementChild);
    viewport.addEventListener("scroll", onScroll, { passive: true });
    document.fonts.ready.then(schedule);
    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      viewport.removeEventListener("scroll", onScroll);
    };
  }, [log, turns, showPhrases]);
}
