import { motion, type HTMLMotionProps, useReducedMotion } from "framer-motion";

export interface OptionCardProps extends Omit<HTMLMotionProps<"button">, "style" | "type"> {
  label: string;
  code?: string;
  state?: "default" | "correct" | "wrong";
  disabled?: boolean;
  compact?: boolean;
}

export default function OptionCard({
  label,
  code,
  state = "default",
  disabled = false,
  compact = false,
  ...rest
}: OptionCardProps) {
  const shouldReduceMotion = useReducedMotion();
  const stateStyles =
    state === "correct"
      ? "border-emerald-300/40 bg-emerald-400/12 text-white shadow-[0_0_38px_rgba(16,185,129,0.22)]"
      : state === "wrong"
        ? "border-rose-300/40 bg-rose-400/12 text-white shadow-[0_0_38px_rgba(244,63,94,0.2)]"
        : "border-cyan-300/18 bg-[linear-gradient(160deg,rgba(9,18,39,0.95),rgba(7,12,26,0.92))] text-white shadow-[0_24px_50px_rgba(2,8,23,0.46)]";

  return (
    <motion.button
      type="button"
      whileHover={!shouldReduceMotion && !disabled && state === "default" ? { scale: 1.01, y: -8 } : {}}
      whileTap={!shouldReduceMotion && !disabled && state === "default" ? { scale: 0.985 } : {}}
      className={`group relative flex w-full items-center overflow-hidden border text-left transition-all duration-200 ${
        disabled && state === "default" ? "cursor-not-allowed opacity-40 grayscale" : "cursor-pointer"
      } ${compact ? "min-h-[124px] rounded-[24px] px-6 py-6" : "min-h-[172px] rounded-[30px] px-8 py-8"} ${stateStyles}`}
      disabled={disabled}
      {...rest}
    >
      <span className={`pointer-events-none absolute top-0 h-px bg-gradient-to-r from-transparent via-cyan-200/80 to-transparent ${compact ? "inset-x-6" : "inset-x-10"}`} />
      <span className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top,rgba(255,255,255,0.14),transparent_48%)]" />
      <span className="pointer-events-none absolute bottom-0 left-0 h-24 w-24 rounded-full bg-cyan-300/10 blur-3xl" />
      <span className={`absolute inline-flex items-center justify-center border border-white/10 bg-white/5 font-mono font-bold text-cyan-100/90 ${compact ? "left-4 top-4 h-10 w-10 rounded-xl text-base" : "left-6 top-6 h-12 w-12 rounded-2xl text-lg"}`}>
        {code ?? "A"}
      </span>
      <span className={`relative z-10 block ${compact ? "pl-12 text-[1.2rem]" : "pl-16 text-[1.95rem]"} font-bold leading-snug`}>
        {label}
      </span>
      <span className={`absolute text-xs uppercase tracking-[0.28em] text-white/40 ${compact ? "bottom-4 right-4" : "bottom-5 right-6"}`}>
        Tap to answer
      </span>
    </motion.button>
  );
}
