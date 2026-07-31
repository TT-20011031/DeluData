import { useMemo } from 'react';

import { QRCodeSVG } from 'qrcode.react';
import {
  ArrowRightLeft,
  BrainCircuit,
  Gift,
  Home,
  Loader2,
  Mic,
  ScanSearch,
  Trophy,
  Volume2,
} from 'lucide-react';

import OptionCard from '../OptionCard';
import { PandaStage } from './PandaStage';
import { useKioskExperienceController } from './useKioskExperienceController';

const THINKING_STEPS = [
  { icon: ScanSearch, title: '倾听心声' },
  { icon: BrainCircuit, title: '翻阅资料' },
  { icon: Volume2, title: '准备讲解' },
];

function joinClasses(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(' ');
}

function shortenLink(link: string) {
  if (link.length <= 52) return link;
  return `${link.slice(0, 26)}...${link.slice(-16)}`;
}

function getSubtitle(controller: ReturnType<typeof useKioskExperienceController>) {
  switch (controller.viewModel.subtitleMode) {
    case 'welcome':
      return {
        eyebrow: '熊猫讲解员',
        text: controller.introText,
        helper: '欢迎语可打断，直接点下方按钮即可切到咨询或答题。',
      };
    case 'live-question':
      return {
        eyebrow: '你正在说',
        text: controller.transcriptDraft || '请直接开口提问',
        helper: controller.speechError || '静默几秒后会自动提交',
      };
    case 'locked-question':
      return {
        eyebrow: '你刚才问',
        text: controller.lastUserQuestion || '问题已锁定',
        helper: '熊猫正在根据知识库组织回答',
      };
    case 'answer':
      return {
        eyebrow: '熊猫讲解员',
        text: controller.lastAssistantAnswer || '答案准备中',
        helper: '播报结束后会自动回到语音咨询',
      };
    case 'quiz':
      return {
        eyebrow: '当前题目',
        text: controller.questionText || '正在准备题目',
        helper: '答对本题即可领奖，答错会自动切到下一题',
      };
    case 'reward':
      return {
        eyebrow: '领奖说明',
        text: '请出示下方二维码与券码给现场工作人员，完成核销后即可领取奖励。',
        helper: '二维码与券码都在下方展示',
      };
    case 'hint':
    default:
      return {
        eyebrow: '熊猫讲解员',
        text: controller.statusMessage,
        helper: '',
      };
  }
}

export default function KioskExperienceShell() {
  const controller = useKioskExperienceController();
  const subtitle = useMemo(() => getSubtitle(controller), [controller]);

  const isQuizMode = controller.viewModel.expansionMode === 'quiz';
  const isRewardMode = controller.viewModel.expansionMode === 'reward';
  const isListeningMode = controller.viewModel.expansionMode === 'listening';
  const isAnswerMode = controller.viewModel.expansionMode === 'sources';
  const isThinkingMode = controller.viewModel.expansionMode === 'thinking';
  const showInfoPanel =
    isQuizMode || isRewardMode || isListeningMode || isAnswerMode || isThinkingMode;

  const actionButtons = [
    controller.viewModel.primaryAction,
    controller.viewModel.secondaryAction,
  ];

  return (
    <div className="relative z-10 h-full overflow-hidden px-4 py-4 md:px-6 md:py-6">
      <PandaStage
        mode={controller.viewModel.heroMode}
        stageLabel={controller.viewModel.stageLabel}
        title={controller.viewModel.stageTitle}
        hint={controller.viewModel.stageHint}
        showPresence={controller.viewModel.showPresence}
        presenceProgress={controller.presence.progress}
        presenceLabel={controller.presence.label}
      />

      <div className="pointer-events-none absolute inset-x-4 top-4 z-30 md:inset-x-6 md:top-6">
        <div className="flex items-start justify-between gap-4">
          <div className="pointer-events-auto inline-flex items-center gap-2 rounded-full border border-white/10 bg-black/32 px-4 py-2 text-sm text-white/82 shadow-[0_12px_34px_rgba(2,8,23,0.28)] backdrop-blur-xl">
            <span className="h-2.5 w-2.5 rounded-full bg-cyan-300" />
            {controller.viewModel.stageLabel}
          </div>

          <button
            type="button"
            onClick={controller.onReturnHome}
            className="pointer-events-auto inline-flex min-h-[3.3rem] items-center gap-2 rounded-full border border-white/10 bg-black/32 px-5 py-3 text-sm font-semibold text-white/90 shadow-[0_12px_34px_rgba(2,8,23,0.28)] backdrop-blur-xl transition-all duration-200 hover:border-cyan-300/20 hover:bg-black/40"
          >
            <Home className="h-4 w-4" />
            返回待机
          </button>
        </div>
      </div>

      <div className="pointer-events-none absolute inset-x-4 bottom-5 z-40 flex justify-center md:inset-x-6 md:bottom-7">
        <div className="pointer-events-auto flex w-full max-w-[1120px] flex-col items-center gap-4">
          <div className="w-full max-w-[860px] text-center rounded-[2rem] border border-white/10 bg-[linear-gradient(180deg,rgba(7,14,29,0.9),rgba(4,8,19,0.88))] px-7 py-6 shadow-[0_24px_80px_rgba(1,8,23,0.42)] backdrop-blur-2xl md:px-10 md:py-7">
            <div className="text-xs uppercase tracking-[0.28em] text-white/38">{subtitle.eyebrow}</div>
            <div className="kiosk-scrollbar mt-3 max-h-[124px] overflow-y-auto text-[1.15rem] font-semibold leading-9 text-white md:max-h-[156px] md:text-[2rem] md:leading-[1.58]">
              {subtitle.text}
            </div>
            {subtitle.helper ? <div className="mt-3 text-sm leading-6 text-white/48 md:text-base">{subtitle.helper}</div> : null}

            {showInfoPanel ? (
              <div className="mt-5 border-t border-white/8 pt-4 text-left">
                {isListeningMode ? (
                  <div className="grid gap-3 md:grid-cols-[1fr_auto_auto] md:items-center">
                    <div className="rounded-[1.2rem] border border-white/10 bg-white/4 px-4 py-3 text-sm leading-7 text-white/62">
                      {controller.speechError
                        ? `语音识别异常：${controller.speechError}`
                        : '正在实时识别你的问题。静默几秒后会自动提交，也可以说“我要玩游戏”切到答题。'}
                    </div>
                    <button type="button" onClick={controller.onSubmitTranscript} className="kiosk-button justify-center text-base md:min-w-[180px]">
                      <Mic className="h-5 w-5" />
                      提交问题
                    </button>
                    <button type="button" onClick={controller.onRetryListening} className="kiosk-button kiosk-button--secondary justify-center text-base md:min-w-[180px]">
                      <ArrowRightLeft className="h-5 w-5" />
                      重试语音
                    </button>
                  </div>
                ) : null}

                {isThinkingMode ? (
                  <div className="flex flex-wrap items-center justify-center gap-3">
                    {THINKING_STEPS.map(({ icon: Icon, title }) => (
                      <div key={title} className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/6 px-4 py-2 text-sm text-white/72">
                        <Icon className="h-4 w-4 text-cyan-100" />
                        {title}
                      </div>
                    ))}
                  </div>
                ) : null}

                {/* isAnswerMode info block removed for tourist view */}

                {isQuizMode ? (
                  <div className="grid gap-3">
                    {controller.quizFeedback ? (
                      <div
                        className={joinClasses(
                          'rounded-[1.2rem] border px-4 py-3 text-sm leading-7',
                          controller.quizStatus === 'correct'
                            ? 'border-emerald-300/24 bg-emerald-300/10 text-emerald-50'
                            : 'border-rose-300/24 bg-rose-300/10 text-rose-50',
                        )}
                      >
                        <div className="font-bold">{controller.quizFeedback.msg}</div>
                        {controller.quizFeedback.exp ? (
                          <div className="mt-2 whitespace-pre-wrap">{controller.quizFeedback.exp}</div>
                        ) : null}
                      </div>
                    ) : null}

                    {controller.quizLoading && !controller.questionText ? (
                      <div className="inline-flex items-center justify-center gap-2 text-sm text-cyan-100">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        正在准备题目
                      </div>
                    ) : null}

                    {!controller.quizLoading && controller.questionText ? (
                      <div className="text-sm text-cyan-100/82">
                        你可以点击选项，也可以直接说 A / B / C / D 或“选A”。
                      </div>
                    ) : null}

                    <div className="grid gap-3 md:grid-cols-2">
                      {controller.options.map((option, index) => (
                        <OptionCard
                          key={`${controller.questionText}-${index}`}
                          code={String.fromCharCode(65 + index)}
                          label={option}
                          compact
                          disabled={controller.selectedOpt !== null && controller.selectedOpt !== index}
                          state={controller.selectedOpt === index ? controller.quizStatus : 'default'}
                          onClick={() => controller.onSelectQuizOption(index)}
                        />
                      ))}
                    </div>
                  </div>
                ) : null}

                {isRewardMode ? (
                  <div className="grid gap-4 md:grid-cols-[220px_1fr] md:items-center">
                    <div className="flex justify-center">
                      <div className="rounded-[1.4rem] bg-white p-3 shadow-[0_0_24px_rgba(255,255,255,0.16)]">
                        <QRCodeSVG
                          value={controller.rewardCard.qrValue}
                          size={188}
                          fgColor="#07111f"
                          level="H"
                          includeMargin={false}
                        />
                      </div>
                    </div>
                    <div className="grid gap-3">
                      <div className="rounded-[1.2rem] border border-white/10 bg-white/4 px-4 py-3">
                        <div className="text-xs uppercase tracking-[0.28em] text-white/38">奖励券码</div>
                        <div className="kiosk-mono mt-2 text-xl font-black text-white md:text-3xl">
                          {controller.rewardCard.couponCode}
                        </div>
                      </div>
                      {controller.rewardCard.qrLink ? (
                        <div className="rounded-[1.2rem] border border-white/10 bg-white/4 px-4 py-3 text-sm text-cyan-100/82">
                          {shortenLink(controller.rewardCard.qrLink)}
                        </div>
                      ) : null}
                      <button type="button" onClick={controller.onFinishReward} className="kiosk-button justify-center text-base md:w-fit md:px-6">
                        <Gift className="h-5 w-5" />
                        完成并回到待机
                      </button>
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>

          <div className="flex flex-wrap items-center justify-center gap-3 rounded-full border border-white/10 bg-[rgba(5,10,22,0.72)] px-4 py-3 shadow-[0_18px_54px_rgba(1,8,23,0.38)] backdrop-blur-2xl md:px-5">
            {actionButtons.map((action) => {
              const isPrimary = action.emphasis === 'primary';
              const isConsult = action.kind === 'consult';
              return (
                <button
                  key={action.kind}
                  type="button"
                  onClick={isConsult ? controller.onConsult : controller.onQuiz}
                  className={joinClasses(
                    'inline-flex min-h-[4rem] items-center gap-2 rounded-full border px-6 py-3 text-base font-bold transition-all duration-200 md:min-w-[190px] md:justify-center md:text-lg',
                    isPrimary
                      ? 'border-cyan-300/18 bg-[linear-gradient(135deg,rgba(57,215,255,0.96),rgba(120,246,255,0.86))] text-[#021018] shadow-[0_18px_40px_rgba(57,215,255,0.2)]'
                      : 'border-white/10 bg-white/6 text-white hover:bg-white/10',
                  )}
                >
                  {isConsult ? <Mic className="h-5 w-5" /> : <Trophy className="h-5 w-5" />}
                  {action.label}
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
