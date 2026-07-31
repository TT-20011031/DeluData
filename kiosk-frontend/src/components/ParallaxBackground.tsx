import { memo, useMemo } from "react";
import { motion, useReducedMotion } from "framer-motion";

export const ParallaxBackground = memo(() => {
  const shouldReduceMotion = useReducedMotion();
  const particles = useMemo(
    () =>
      Array.from({ length: 16 }, (_, index) => ({
        key: index,
        size: 8 + (index % 5) * 4,
        left: `${(index * 19 + 7) % 100}%`,
        top: `${(index * 13 + 11) % 100}%`,
        duration: 12 + index * 1.4,
        delay: index * 0.35,
      })),
    [],
  );

  return (
    <div className="pointer-events-none fixed inset-0 z-0 overflow-hidden bg-[#050816]">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,rgba(57,215,255,0.16),transparent_34%),radial-gradient(circle_at_84%_10%,rgba(247,185,85,0.1),transparent_22%),radial-gradient(circle_at_50%_120%,rgba(113,243,164,0.12),transparent_34%)]" />
      <div className="kiosk-grid-mask absolute inset-0 opacity-45" />
      <div className="absolute inset-0 bg-[linear-gradient(90deg,transparent_0%,rgba(57,215,255,0.04)_48%,transparent_52%,transparent_100%)] opacity-50" />

      <motion.div
        className="absolute -left-32 top-10 h-[28rem] w-[28rem] rounded-full bg-cyan-400/16 blur-[110px]"
        animate={shouldReduceMotion ? undefined : { x: [0, 90, 0], y: [0, 24, 0] }}
        transition={{ duration: 18, repeat: Infinity, ease: "easeInOut" }}
      />
      <motion.div
        className="absolute right-[-5rem] top-[12%] h-[26rem] w-[26rem] rounded-full bg-amber-300/10 blur-[130px]"
        animate={shouldReduceMotion ? undefined : { x: [0, -70, 0], y: [0, 40, 0] }}
        transition={{ duration: 20, repeat: Infinity, ease: "easeInOut" }}
      />
      <motion.div
        className="absolute bottom-[-10rem] left-[20%] h-[32rem] w-[32rem] rounded-full bg-emerald-300/10 blur-[150px]"
        animate={shouldReduceMotion ? undefined : { x: [0, 54, 0], y: [0, -48, 0] }}
        transition={{ duration: 24, repeat: Infinity, ease: "easeInOut" }}
      />

      <motion.div
        className="absolute left-1/2 top-1/2 h-[56vw] w-[56vw] -translate-x-1/2 -translate-y-1/2 rounded-full border border-cyan-300/10"
        animate={shouldReduceMotion ? undefined : { rotate: 360 }}
        transition={{ duration: 60, repeat: Infinity, ease: "linear" }}
      />
      <motion.div
        className="absolute left-1/2 top-1/2 h-[42vw] w-[42vw] -translate-x-1/2 -translate-y-1/2 rounded-full border border-cyan-300/8"
        animate={shouldReduceMotion ? undefined : { rotate: -360 }}
        transition={{ duration: 42, repeat: Infinity, ease: "linear" }}
      />

      <motion.div
        className="absolute left-1/2 top-[-14%] h-[140%] w-[32%] -translate-x-1/2 bg-[linear-gradient(180deg,transparent,rgba(57,215,255,0.08),transparent)] blur-3xl"
        animate={shouldReduceMotion ? undefined : { rotate: [-10, 12, -10] }}
        transition={{ duration: 18, repeat: Infinity, ease: "easeInOut" }}
      />

      {particles.map((particle) => (
        <motion.span
          key={particle.key}
          className="absolute rounded-full border border-white/14 bg-white/8 shadow-[0_0_16px_rgba(135,241,255,0.18)] backdrop-blur-sm"
          style={{
            width: `${particle.size}px`,
            height: `${particle.size}px`,
            left: particle.left,
            top: particle.top,
          }}
          animate={
            shouldReduceMotion
              ? undefined
              : {
                  y: [0, -24 - particle.key * 4, 0],
                  x: [0, particle.key % 2 === 0 ? 8 : -8, 0],
                  opacity: [0.18, 0.8, 0.18],
                }
          }
          transition={{
            duration: particle.duration,
            repeat: Infinity,
            ease: "easeInOut",
            delay: particle.delay,
          }}
        />
      ))}

      <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,transparent_42%,rgba(2,4,13,0.78)_100%)]" />
    </div>
  );
});
