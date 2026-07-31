import { useState } from "react";
import { Navigate } from "react-router-dom";
import {
  BookOpen,
  Mic,
  MonitorSmartphone,
  RefreshCw,
  ScanLine,
  Search,
  Ticket,
  Trophy,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tabs, TabsContent } from "@/components/ui/tabs";
import { StyledTabsNav, type StyledTabItem } from "@/components/ui/styled-tabs";
import { useAuthStore } from "@/stores/authStore";

import CouponTab from "./CouponTab";
import DeviceTab from "./DeviceTab";
import KnowledgeScopeTab from "./KnowledgeScopeTab";
import QuizTab from "./QuizTab";
import RedeemTab from "./RedeemTab";
import RewardTab from "./RewardTab";
import VoiceTab from "./VoiceTab";

const TAB_ITEMS: StyledTabItem[] = [
  { value: "devices", label: "设备管理", icon: <MonitorSmartphone className="h-4 w-4" /> },
  { value: "voice", label: "唤醒词与音色", icon: <Mic className="h-4 w-4" /> },
  { value: "quiz", label: "题库管理", icon: <BookOpen className="h-4 w-4" /> },
  { value: "scope", label: "检索范围", icon: <Search className="h-4 w-4" /> },
  { value: "reward", label: "奖励规则", icon: <Trophy className="h-4 w-4" /> },
  { value: "coupons", label: "券模板与库存", icon: <Ticket className="h-4 w-4" /> },
  { value: "redeem", label: "核销记录", icon: <ScanLine className="h-4 w-4" /> },
];

export default function ScienceAdminPage() {
  const { user } = useAuthStore();
  const [refreshKey, setRefreshKey] = useState(0);
  const [activeTab, setActiveTab] = useState("devices");

    if (!user?.permissions?.some((code) => code === '*' || code === 'config:manage')) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="flex-1 p-6 bg-manus overflow-auto">
      <div className="max-w-7xl mx-auto space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-manus-text">科技馆管理</h1>
            <p className="text-sm text-manus-muted mt-1">设备、激活、音色、题库、检索范围、奖励与核销</p>
          </div>
          <Button
            variant="outline"
            onClick={() => setRefreshKey((k) => k + 1)}
            className="bg-manus-tertiary border-manus-border"
          >
            <RefreshCw className="h-4 w-4 mr-2" />
            刷新全部
          </Button>
        </div>

        <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
          <StyledTabsNav items={TAB_ITEMS} onValueChange={setActiveTab} />
          <TabsContent value="devices">{activeTab === "devices" ? <DeviceTab key={`dev-${refreshKey}`} /> : null}</TabsContent>
          <TabsContent value="voice">{activeTab === "voice" ? <VoiceTab key={`voice-${refreshKey}`} /> : null}</TabsContent>
          <TabsContent value="quiz">{activeTab === "quiz" ? <QuizTab key={`quiz-${refreshKey}`} /> : null}</TabsContent>
          <TabsContent value="scope">{activeTab === "scope" ? <KnowledgeScopeTab key={`scope-${refreshKey}`} /> : null}</TabsContent>
          <TabsContent value="reward">{activeTab === "reward" ? <RewardTab key={`reward-${refreshKey}`} /> : null}</TabsContent>
          <TabsContent value="coupons">{activeTab === "coupons" ? <CouponTab key={`coupon-${refreshKey}`} /> : null}</TabsContent>
          <TabsContent value="redeem">{activeTab === "redeem" ? <RedeemTab key={`redeem-${refreshKey}`} /> : null}</TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
