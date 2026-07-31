import type { FlowIntent, PageName } from '../../store/session';

export type HeroMode =
  | 'idle'
  | 'speaking'
  | 'listening'
  | 'thinking'
  | 'quiz'
  | 'celebration';

export type SubtitleMode =
  | 'hint'
  | 'welcome'
  | 'live-question'
  | 'locked-question'
  | 'answer'
  | 'quiz'
  | 'reward';

export type ExpansionMode =
  | 'examples'
  | 'welcome'
  | 'listening'
  | 'thinking'
  | 'sources'
  | 'quiz'
  | 'reward';

export interface DerivedStageRuntimeFlags {
  isWelcomePlaying: boolean;
  isMicActive: boolean;
  isTtsPlaying: boolean;
  isStarting: boolean;
  countdown: number | null;
  hasReward: boolean;
  transcriptDraft: string;
}

type ActionModel = {
  kind: FlowIntent;
  label: string;
  emphasis: 'primary' | 'secondary';
};

export interface DerivedStageViewModel {
  heroMode: HeroMode;
  subtitleMode: SubtitleMode;
  expansionMode: ExpansionMode;
  primaryAction: ActionModel;
  secondaryAction: ActionModel;
  canInterrupt: boolean;
  showPresence: boolean;
  stageLabel: string;
  stageTitle: string;
  stageHint: string;
  tone: 'default' | 'warm' | 'success';
}

function getActionModels(intent: FlowIntent): Pick<DerivedStageViewModel, 'primaryAction' | 'secondaryAction'> {
  if (intent === 'quiz') {
    return {
      primaryAction: { kind: 'quiz', label: '答题挑战', emphasis: 'primary' },
      secondaryAction: { kind: 'consult', label: '咨询数字人', emphasis: 'secondary' },
    };
  }

  return {
    primaryAction: { kind: 'consult', label: '咨询数字人', emphasis: 'primary' },
    secondaryAction: { kind: 'quiz', label: '答题挑战', emphasis: 'secondary' },
  };
}

export function getDerivedStageViewModel(
  currentPage: PageName,
  intent: FlowIntent,
  flags: DerivedStageRuntimeFlags,
): DerivedStageViewModel {
  const actionModels = getActionModels(intent);
  const canInterrupt =
    currentPage === 'PersonaIntro'
      ? flags.isWelcomePlaying || flags.isTtsPlaying || flags.isMicActive
      : currentPage !== 'Attract' && currentPage !== 'Activation';

  switch (currentPage) {
    case 'Attract':
      return {
        ...actionModels,
        heroMode: 'idle',
        subtitleMode: 'hint',
        expansionMode: 'examples',
        canInterrupt: false,
        showPresence: true,
        stageLabel: flags.isStarting ? '启动中' : '待机接待',
        stageTitle: flags.isStarting ? '熊猫正在为你打开互动' : '熊猫讲解员已就位',
        stageHint: flags.isStarting ? '熊猫正在打扮，马上就来' : '靠近屏幕或点击下方按钮即可开始',
        tone: flags.isStarting ? 'warm' : 'default',
      };
    case 'PersonaIntro':
      return {
        ...actionModels,
        heroMode: 'speaking',
        subtitleMode: 'welcome',
        expansionMode: 'welcome',
        canInterrupt,
        showPresence: false,
        stageLabel: '欢迎阶段',
        stageTitle: '熊猫正在打招呼',
        stageHint: canInterrupt ? '你可以直接打断，切到咨询或答题' : '欢迎语即将结束',
        tone: 'warm',
      };
    case 'Listening':
      return {
        ...actionModels,
        heroMode: 'listening',
        subtitleMode: 'live-question',
        expansionMode: 'listening',
        canInterrupt,
        showPresence: false,
        stageLabel: '语音咨询',
        stageTitle: flags.transcriptDraft ? '我在认真听你说' : '请直接开口提问',
        stageHint: flags.countdown ? `保持说话，静默后会自动提交` : '等待你的问题',
        tone: 'success',
      };
    case 'Thinking':
      return {
        ...actionModels,
        heroMode: 'thinking',
        subtitleMode: 'locked-question',
        expansionMode: 'thinking',
        canInterrupt,
        showPresence: false,
        stageLabel: '回答生成',
        stageTitle: '熊猫正在整理答案',
        stageHint: '正在为你翻阅百宝箱寻找答案',
        tone: 'warm',
      };
    case 'Answer':
      return {
        ...actionModels,
        heroMode: 'speaking',
        subtitleMode: 'answer',
        expansionMode: 'sources',
        canInterrupt,
        showPresence: false,
        stageLabel: '知识回答',
        stageTitle: flags.isTtsPlaying ? '熊猫正在讲解' : '讲解已准备完成',
        stageHint: '讲解结束后会自动回到语音咨询，也可以随时切去答题挑战',
        tone: 'success',
      };
    case 'Quiz':
      return {
        ...getActionModels('quiz'),
        heroMode: 'quiz',
        subtitleMode: 'quiz',
        expansionMode: 'quiz',
        canInterrupt,
        showPresence: false,
        stageLabel: '答题挑战',
        stageTitle: '答对本题即可领奖',
        stageHint: '答错会切下一题，说字母或点击都可作答',
        tone: 'warm',
      };
    case 'Reward':
      return {
        ...getActionModels('quiz'),
        heroMode: 'celebration',
        subtitleMode: 'reward',
        expansionMode: 'reward',
        canInterrupt,
        showPresence: false,
        stageLabel: '奖励领取',
        stageTitle: flags.hasReward ? '挑战成功，奖励已解锁' : '奖励信息准备中',
        stageHint: '请在下方展示二维码与券码给现场工作人员',
        tone: 'success',
      };
    case 'Activation':
    default:
      return {
        ...actionModels,
        heroMode: 'idle',
        subtitleMode: 'hint',
        expansionMode: 'examples',
        canInterrupt: false,
        showPresence: false,
        stageLabel: '未激活',
        stageTitle: '设备等待激活',
        stageHint: '激活后进入单页数字人舞台',
        tone: 'default',
      };
  }
}
