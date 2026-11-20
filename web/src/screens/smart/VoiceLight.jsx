import { useEffect, useRef } from "react";
import voiceLight from "../../../assets/voice-light.png";

export function VoiceLight({ reply, active }) {
  const canvas = useRef(null);
  const image = useRef(null);
  const replyStartedAt = useRef(null);

  useEffect(() => {
    replyStartedAt.current = reply ? performance.now() : null;
  }, [reply]);

  useEffect(() => {
    if (!active) return;
    const surface = canvas.current;
    const texture = image.current;
    const context = surface.getContext("2d");
    if (!context) return;
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0;

    function paint(now) {
      if (texture.naturalWidth) {
        const { width, height } = surface;
        const age = Math.max(0, (now - (replyStartedAt.current ?? now)) / 1000);
        const pulse = Math.min(1, age * 9) * Math.exp(-age * 0.8);
        const time = now / 1000;
        const strength = 0.35 + pulse * 0.65;
        context.clearRect(0, 0, width, height);

        // Deform the reference texture in thin horizontal bands to keep its palette and soft edges.
        for (let y = 0; y < height; y += 2) {
          const band = y / height;
          const wave = Math.sin(band * 28 + time * 1.6) + 0.55 * Math.sin(band * 63 - time * 2.2);
          const offset = wave * width * 0.075 * strength;
          const stretch = 1 + Math.sin(band * 17 - time * 1.35) * 0.18 * strength;
          const sourceY = band * texture.naturalHeight;
          const sourceHeight = 2 / height * texture.naturalHeight;
          context.drawImage(texture, 0, sourceY, texture.naturalWidth, sourceHeight,
            offset + width * (1 - stretch) / 2, y, width * stretch, 2);
        }
        surface.dataset.rendered = "true";
      }
      frame = requestAnimationFrame(paint);
    }

    function syncMotion() {
      cancelAnimationFrame(frame);
      if (motion.matches) delete surface.dataset.rendered;
      else frame = requestAnimationFrame(paint);
    }

    syncMotion();
    motion.addEventListener("change", syncMotion);
    return () => {
      cancelAnimationFrame(frame);
      motion.removeEventListener("change", syncMotion);
    };
  }, [active]);

  return (
    <div className="voice-light" aria-hidden="true">
      <canvas ref={canvas} width="416" height="400" />
      <img ref={image} src={voiceLight} alt="" />
    </div>
  );
}
