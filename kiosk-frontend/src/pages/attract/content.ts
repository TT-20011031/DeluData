import {
  Compass,
  Gift,
  Map,
  MessageCircleQuestion,
  Mic,
  ScanFace,
  ShoppingBag,
  Sparkles,
  Trophy,
  type LucideIcon,
} from "lucide-react";

export type AttractBadge = {
  label: string;
  className: string;
};

export type AttractFeatureCard = {
  icon: LucideIcon;
  title: string;
  description: string;
};

export type AttractQuestionExample = {
  icon: LucideIcon;
  text: string;
};

export type AttractStartStep = {
  icon: LucideIcon;
  title: string;
  description: string;
};

export const HERO_BADGES: AttractBadge[] = [
  { label: "AI 导览", className: "left-[-1.5rem] top-10" },
  { label: "自动感应", className: "right-[-2rem] top-14" },
  { label: "语音问答", className: "bottom-10 left-[-2rem]" },
  { label: "答题领奖", className: "bottom-12 right-[-2rem]" },
];

export const FEATURE_CARDS: AttractFeatureCard[] = [
  {
    icon: Compass,
    title: "智能导览",
    description: "问展项、问亮点、问馆内内容，系统会直接讲给你听。",
  },
  {
    icon: ShoppingBag,
    title: "导购推荐",
    description: "听讲解的同时，顺手看看相关文创和延展内容。",
  },
  {
    icon: Trophy,
    title: "答题领奖",
    description: "互动结束还能继续挑战答题，完成后现场扫码领奖。",
  },
];

export const QUESTION_EXAMPLES: AttractQuestionExample[] = [
  { icon: MessageCircleQuestion, text: "这个展项是做什么的？" },
  { icon: Map, text: "馆里最值得看的区域在哪里？" },
  { icon: Gift, text: "有没有适合带走的文创推荐？" },
];

export const START_STEPS: AttractStartStep[] = [
  {
    icon: ScanFace,
    title: "走近大屏",
    description: "站到屏幕前，系统会自动感应你是否准备开始。",
  },
  {
    icon: Mic,
    title: "直接开口问",
    description: "不需要输入，不需要找菜单，直接问你想知道的内容。",
  },
  {
    icon: Sparkles,
    title: "继续玩下去",
    description: "听完还能继续答题闯关，把体验做成一轮完整互动。",
  },
];
