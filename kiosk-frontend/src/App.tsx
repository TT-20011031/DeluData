import { useEffect } from 'react';

import ActivationPage from './pages/ActivationPage';
import { getDeviceToken } from './api/config';
import { ParallaxBackground } from './components/ParallaxBackground';
import KioskExperienceShell from './components/kiosk/KioskExperienceShell';
import { useSessionStore } from './store/session';

function App() {
  const currentPage = useSessionStore((state) => state.currentPage);
  const setPage = useSessionStore((state) => state.setPage);
  const hasDeviceToken = Boolean(getDeviceToken());

  useEffect(() => {
    if (hasDeviceToken && currentPage === 'Activation') {
      setPage('Attract');
    }
    if (!hasDeviceToken && currentPage !== 'Activation') {
      setPage('Activation');
    }
  }, [currentPage, hasDeviceToken, setPage]);

  return (
    <div className="relative h-screen w-screen overflow-hidden bg-kiosk-base font-sans text-kiosk-text">
      <ParallaxBackground />
      {!hasDeviceToken || currentPage === 'Activation' ? <ActivationPage /> : <KioskExperienceShell />}
    </div>
  );
}

export default App;
