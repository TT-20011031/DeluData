import { motion, useReducedMotion } from 'framer-motion';
import {
  BrainCircuit,
  MessageSquareQuote,
  Mic,
  PartyPopper,
  Sparkles,
  Volume2,
} from 'lucide-react';

import type { HeroMode } from './stageViewModel';

type PandaStageProps = {
  mode: HeroMode;
  stageLabel: string;
  title: string;
  hint: string;
  showPresence: boolean;
  presenceProgress: number;
  presenceLabel: string;
};

function joinClasses(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(' ');
}

function getStageAccent(mode: HeroMode) {
  switch (mode) {
    case 'speaking':
      return {
        chip: 'bg-amber-300/12 text-amber-50 border-amber-300/16',
        glow: 'from-amber-300/24 via-cyan-300/10 to-transparent',
        icon: Volume2,
        iconLabel: '播报中',
      };
    case 'listening':
      return {
        chip: 'bg-emerald-300/12 text-emerald-50 border-emerald-300/16',
        glow: 'from-emerald-300/20 via-cyan-300/10 to-transparent',
        icon: Mic,
        iconLabel: '聆听中',
      };
    case 'thinking':
      return {
        chip: 'bg-fuchsia-300/12 text-fuchsia-50 border-fuchsia-300/16',
        glow: 'from-fuchsia-300/22 via-cyan-300/10 to-transparent',
        icon: BrainCircuit,
        iconLabel: '思考中',
      };
    case 'quiz':
      return {
        chip: 'bg-amber-300/12 text-amber-50 border-amber-300/16',
        glow: 'from-amber-300/24 via-fuchsia-300/10 to-transparent',
        icon: MessageSquareQuote,
        iconLabel: '答题中',
      };
    case 'celebration':
      return {
        chip: 'bg-emerald-300/12 text-emerald-50 border-emerald-300/16',
        glow: 'from-emerald-300/24 via-amber-300/14 to-transparent',
        icon: PartyPopper,
        iconLabel: '已解锁',
      };
    case 'idle':
    default:
      return {
        chip: 'bg-cyan-300/12 text-cyan-50 border-cyan-300/16',
        glow: 'from-cyan-300/20 via-cyan-300/10 to-transparent',
        icon: Sparkles,
        iconLabel: '待命中',
      };
  }
}

function PandaMascot({ mode }: { mode: HeroMode }) {
  const shouldReduceMotion = useReducedMotion();
  const isSpeaking = mode === 'speaking';
  const isListening = mode === 'listening';
  const isThinking = mode === 'thinking';
  const isCelebration = mode === 'celebration';

  return (
    <motion.div
      className="relative h-[27rem] w-[20rem] md:h-[35rem] md:w-[25rem]"
      animate={
        shouldReduceMotion
          ? undefined
          : {
            y: mode === 'idle' ? [0, -10, 0] : [0, -4, 0],
            rotate: isCelebration ? [0, -2.2, 2.2, 0] : [0, -1, 1, 0],
          }
      }
      transition={{ duration: isCelebration ? 2.8 : 4.8, repeat: Infinity, ease: 'easeInOut' }}
    >
      <div className="absolute left-1/2 top-[10.2rem] h-[16rem] w-[13rem] -translate-x-1/2 rounded-[48%] bg-slate-950/94 shadow-[0_32px_90px_rgba(1,8,23,0.45)] md:top-[13.2rem] md:h-[20rem] md:w-[16rem]" />
      <div className="absolute left-1/2 top-[11.4rem] h-[13.8rem] w-[10rem] -translate-x-1/2 rounded-[46%] border border-white/14 bg-[radial-gradient(circle_at_50%_18%,rgba(255,255,255,1),rgba(239,248,255,0.98)_62%,rgba(220,229,240,0.96)_100%)] md:top-[14.8rem] md:h-[16.8rem] md:w-[12.2rem]" />
      <div className="absolute left-[1.25rem] top-[13.2rem] h-[7rem] w-[5rem] rotate-[24deg] rounded-[50%] bg-slate-950 md:left-[1.6rem] md:top-[16.8rem] md:h-[8rem] md:w-[5.8rem]" />
      <div className="absolute right-[1.25rem] top-[13.2rem] h-[7rem] w-[5rem] rotate-[-24deg] rounded-[50%] bg-slate-950 md:right-[1.6rem] md:top-[16.8rem] md:h-[8rem] md:w-[5.8rem]" />
      <div className="absolute left-[4rem] bottom-[1.6rem] h-[3.2rem] w-[3.8rem] rounded-full bg-slate-950 md:left-[5rem] md:bottom-[2rem] md:h-[3.8rem] md:w-[4.5rem]" />
      <div className="absolute right-[4rem] bottom-[1.6rem] h-[3.2rem] w-[3.8rem] rounded-full bg-slate-950 md:right-[5rem] md:bottom-[2rem] md:h-[3.8rem] md:w-[4.5rem]" />

      <div className="absolute left-1/2 top-0 h-[15rem] w-[15rem] -translate-x-1/2 rounded-[46%] border border-white/40 bg-[radial-gradient(circle_at_50%_24%,rgba(255,255,255,1),rgba(239,248,255,0.98)_58%,rgba(213,225,240,0.96)_100%)] shadow-[0_30px_70px_rgba(1,8,22,0.44)] md:h-[18rem] md:w-[18rem]" />
      <div className="absolute left-[2rem] top-[0.8rem] h-[4.8rem] w-[4.8rem] rounded-full bg-slate-950 shadow-[0_0_40px_rgba(2,8,23,0.4)] md:left-[2.4rem] md:top-[1rem] md:h-[5.6rem] md:w-[5.6rem]" />
      <div className="absolute right-[2rem] top-[0.8rem] h-[4.8rem] w-[4.8rem] rounded-full bg-slate-950 shadow-[0_0_40px_rgba(2,8,23,0.4)] md:right-[2.4rem] md:top-[1rem] md:h-[5.6rem] md:w-[5.6rem]" />

      <div className="absolute left-[3.1rem] top-[5.2rem] h-[5rem] w-[4rem] rotate-[-18deg] rounded-[50%] bg-slate-950 md:left-[3.9rem] md:top-[6.2rem] md:h-[6rem] md:w-[4.8rem]" />
      <div className="absolute right-[3.1rem] top-[5.2rem] h-[5rem] w-[4rem] rotate-[18deg] rounded-[50%] bg-slate-950 md:right-[3.9rem] md:top-[6.2rem] md:h-[6rem] md:w-[4.8rem]" />

      <motion.div
        className="absolute left-[5rem] top-[7.3rem] h-[1.15rem] w-[1.15rem] rounded-full bg-white md:left-[6.1rem] md:top-[8.9rem] md:h-[1.35rem] md:w-[1.35rem]"
        animate={shouldReduceMotion ? undefined : { scaleY: isListening ? [1, 0.5, 1] : [1, 1, 0.18, 1] }}
        transition={{ duration: 3.4, repeat: Infinity, ease: 'easeInOut' }}
      />
      <motion.div
        className="absolute right-[5rem] top-[7.3rem] h-[1.15rem] w-[1.15rem] rounded-full bg-white md:right-[6.1rem] md:top-[8.9rem] md:h-[1.35rem] md:w-[1.35rem]"
        animate={shouldReduceMotion ? undefined : { scaleY: isListening ? [1, 0.5, 1] : [1, 1, 0.18, 1] }}
        transition={{ duration: 3.4, repeat: Infinity, ease: 'easeInOut', delay: 0.08 }}
      />

      <div className="absolute left-1/2 top-[9.2rem] h-[1.35rem] w-[1.9rem] -translate-x-1/2 rounded-[45%] bg-slate-950 md:top-[11rem] md:h-[1.6rem] md:w-[2.2rem]" />
      <motion.div
        className="absolute left-1/2 top-[10.6rem] h-[1rem] w-[2.5rem] -translate-x-1/2 rounded-b-[1.2rem] border-b-[5px] border-slate-950 md:top-[12.7rem] md:h-[1.15rem] md:w-[3rem]"
        animate={
          shouldReduceMotion
            ? undefined
            : isSpeaking
              ? { scaleY: [0.7, 1.2, 0.7] }
              : isThinking
                ? { y: [0, 3, 0] }
                : { scaleX: [1, 0.95, 1] }
        }
        transition={{ duration: 1.3, repeat: Infinity, ease: 'easeInOut' }}
      />

      <div className="absolute left-[4.9rem] top-[9.9rem] h-3 w-5 rounded-full bg-rose-300/40 blur-sm md:left-[6rem] md:top-[12rem] md:h-4 md:w-6" />
      <div className="absolute right-[4.9rem] top-[9.9rem] h-3 w-5 rounded-full bg-rose-300/40 blur-sm md:right-[6rem] md:top-[12rem] md:h-4 md:w-6" />

      {isListening ? (
        <>
          <motion.span
            className="absolute left-[-0.5rem] top-[8.2rem] h-16 w-16 rounded-full border border-emerald-200/30"
            animate={shouldReduceMotion ? undefined : { scale: [0.8, 1.25], opacity: [0.8, 0] }}
            transition={{ duration: 1.8, repeat: Infinity, ease: 'easeOut' }}
          />
          <motion.span
            className="absolute right-[-0.5rem] top-[8.2rem] h-16 w-16 rounded-full border border-emerald-200/30"
            animate={shouldReduceMotion ? undefined : { scale: [0.8, 1.25], opacity: [0.8, 0] }}
            transition={{ duration: 1.8, repeat: Infinity, ease: 'easeOut', delay: 0.2 }}
          />
        </>
      ) : null}

      {isCelebration ? (
        <>
          <motion.span
            className="absolute left-[0.5rem] top-[3rem] h-4 w-4 rounded-full bg-amber-300"
            animate={shouldReduceMotion ? undefined : { y: [0, -18, 6, 0], x: [0, -10, 6, 0], opacity: [1, 1, 0.4, 1] }}
            transition={{ duration: 2.1, repeat: Infinity, ease: 'easeInOut' }}
          />
          <motion.span
            className="absolute right-[1rem] top-[2rem] h-3 w-3 rounded-full bg-emerald-300"
            animate={shouldReduceMotion ? undefined : { y: [0, -22, 8, 0], x: [0, 12, -8, 0], opacity: [1, 0.9, 0.35, 1] }}
            transition={{ duration: 2.4, repeat: Infinity, ease: 'easeInOut', delay: 0.2 }}
          />
        </>
      ) : null}
    </motion.div>
  );
}

export function PandaStage({
  mode,
  stageLabel,
  title,
  hint,
  showPresence,
  presenceProgress,
  presenceLabel,
}: PandaStageProps) {
  const shouldReduceMotion = useReducedMotion();
  const accent = getStageAccent(mode);
  const AccentIcon = accent.icon;
  const presenceDegrees = Math.max(12, Math.round(presenceProgress * 360));

  return (
    <section className="kiosk-panel relative h-full overflow-hidden px-6 py-6 md:px-10 md:py-8">
      <div className={joinClasses('absolute inset-0 bg-gradient-to-br opacity-90', accent.glow)} />
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_10%,rgba(255,255,255,0.08),transparent_32%),radial-gradient(circle_at_50%_90%,rgba(2,8,23,0.34),transparent_58%)]" />

      <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex items-center justify-between px-6 pt-20 md:px-10 md:pt-24">
        <div className={joinClasses('inline-flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-semibold tracking-[0.18em] shadow-[0_12px_34px_rgba(2,8,23,0.24)] backdrop-blur-xl', accent.chip)}>
          <span className="h-2.5 w-2.5 rounded-full bg-current" />
          {stageLabel}
        </div>
        <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-black/22 px-4 py-2 text-sm text-white/76 shadow-[0_12px_34px_rgba(2,8,23,0.24)] backdrop-blur-xl">
          <AccentIcon className="h-4 w-4" />
          {accent.iconLabel}
        </div>
      </div>

      <div className="relative z-10 flex h-full flex-col items-center justify-start pb-[14rem] pt-[1.5rem] md:pb-[16rem] md:pt-[2.5rem]">
        {/* Title area now positioned ABOVE the panda */}
        <div className="mb-[-1rem] max-w-3xl text-center md:mb-[-1.5rem] z-20 relative">
          <h1 className="kiosk-heading text-[2.5rem] font-black md:text-[4.5rem] tracking-[0.1em] text-transparent bg-clip-text bg-gradient-to-b from-white via-cyan-100 to-cyan-300 drop-shadow-[0_0_15px_rgba(57,215,255,0.6)]">
            {title}
          </h1>
          <p className="mt-4 text-sm leading-7 text-white/66 md:text-lg md:leading-8">{hint}</p>
          {showPresence ? (
            <div className="mt-4 inline-flex items-center gap-2 rounded-full border border-emerald-300/20 bg-emerald-300/10 px-4 py-2 text-sm text-emerald-50/88">
              <span className="h-2.5 w-2.5 rounded-full bg-emerald-300" />
              {presenceLabel}
            </div>
          ) : null}
        </div>

        {/* Panda stage slightly scaled down with origin-top to avoid overlapping with bottom box */}
        <div className="relative flex h-[30rem] w-[30rem] items-center justify-center md:h-[40rem] md:w-[40rem] scale-[0.85] md:scale-[0.9] origin-top mt-[2rem] md:mt-[3rem]">
          <motion.span
            className="absolute inset-[3%] rounded-full border border-white/8"
            animate={shouldReduceMotion ? undefined : { rotate: 360 }}
            transition={{ duration: 44, repeat: Infinity, ease: 'linear' }}
          />
          <motion.span
            className="absolute inset-[12%] rounded-full border border-cyan-200/10"
            animate={shouldReduceMotion ? undefined : { rotate: -360 }}
            transition={{ duration: 32, repeat: Infinity, ease: 'linear' }}
          />

          <span className="absolute inset-[16%] rounded-full bg-[radial-gradient(circle_at_top,rgba(255,255,255,0.16),rgba(255,255,255,0.02)_48%,rgba(3,9,19,0.82)_100%)] shadow-[0_50px_140px_rgba(1,8,23,0.5)]" />

          {showPresence ? (
            <div
              className="absolute inset-[10%] rounded-full"
              style={{
                background: `conic-gradient(from 0deg, rgba(113,243,164,0.92) 0deg ${presenceDegrees}deg, rgba(57,215,255,0.08) ${presenceDegrees}deg 360deg)`,
              }}
            />
          ) : null}

          <div className="absolute inset-[14%] rounded-full bg-[radial-gradient(circle_at_50%_28%,rgba(9,18,39,0.86),rgba(3,7,18,0.98)_72%)]" />

          {mode === 'speaking' ? (
            <>
              <motion.span
                className="absolute left-[13%] top-[38%] h-20 w-20 rounded-full border border-amber-200/24"
                animate={shouldReduceMotion ? undefined : { scale: [0.7, 1.2], opacity: [0.85, 0] }}
                transition={{ duration: 1.4, repeat: Infinity, ease: 'easeOut' }}
              />
              <motion.span
                className="absolute right-[13%] top-[38%] h-20 w-20 rounded-full border border-amber-200/24"
                animate={shouldReduceMotion ? undefined : { scale: [0.7, 1.2], opacity: [0.85, 0] }}
                transition={{ duration: 1.4, repeat: Infinity, ease: 'easeOut', delay: 0.15 }}
              />
            </>
          ) : null}

          {mode === 'thinking' ? (
            <motion.div
              className="absolute inset-[20%] rounded-full border border-fuchsia-200/14"
              animate={shouldReduceMotion ? undefined : { rotate: 360 }}
              transition={{ duration: 4.2, repeat: Infinity, ease: 'linear' }}
            />
          ) : null}

          <PandaMascot mode={mode} />
        </div>


      </div>
    </section>
  );
}
