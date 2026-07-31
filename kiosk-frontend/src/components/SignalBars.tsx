import { motion, useReducedMotion } from "framer-motion";

type SignalBarsProps = {
  bars?: number;
  className?: string;
  colorClassName?: string;
  active?: boolean;
};

function joinClasses(...classes: Array<string | undefined | false>) {
  return classes.filter(Boolean).join(" ");
}

export function SignalBars({
  bars = 10,
  className,
  colorClassName = "bg-cyan-300",
  active = true,
}: SignalBarsProps) {
  const shouldReduceMotion = useReducedMotion();
  const barHeights = Array.from({ length: bars }, (_, index) => 18 + (index % 5) * 8);

  return (
    <div className={joinClasses("flex h-16 items-end gap-2", className)} aria-hidden="true">
      {barHeights.map((height, index) =>
        shouldReduceMotion || !active ? (
          <span
            key={index}
            className={joinClasses("w-2 rounded-full opacity-75", colorClassName)}
            style={{ height }}
          />
        ) : (
          <motion.span
            key={index}
            className={joinClasses("w-2 origin-bottom rounded-full", colorClassName)}
            style={{ height }}
            animate={{
              scaleY: [0.42 + (index % 3) * 0.08, 1, 0.55 + (index % 4) * 0.08],
              opacity: [0.45, 1, 0.6],
            }}
            transition={{
              duration: 1 + index * 0.08,
              ease: "easeInOut",
              repeat: Infinity,
              repeatType: "mirror",
              delay: index * 0.06,
            }}
          />
        ),
      )}
    </div>
  );
}
