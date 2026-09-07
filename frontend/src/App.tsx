import { useState } from "react";
import { ThemeProvider } from "./context/ThemeContext";
import { CallScreen } from "./components/CallScreen";
import { IntroScreen } from "./components/IntroScreen";

function AppContent() {
  const [showIntro, setShowIntro] = useState(true);

  return showIntro ? (
    <IntroScreen onStart={() => setShowIntro(false)} />
  ) : (
    <CallScreen onReturnToIntro={() => setShowIntro(true)} />
  );
}

function App() {
  return (
    <ThemeProvider>
      <AppContent />
    </ThemeProvider>
  );
}

export default App;