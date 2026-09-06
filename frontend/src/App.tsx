import { useState } from "react";
import { CallScreen } from "./components/CallScreen";
import { IntroScreen } from "./components/IntroScreen";

function App() {
  const [showIntro, setShowIntro] = useState(true);

  const handleStart = () => setShowIntro(false);

  return showIntro ? (
    <IntroScreen onStart={handleStart} />
  ) : (
    <CallScreen />
  );
}

export default App;