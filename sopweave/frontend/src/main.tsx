import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import { AiRegistryProvider } from "./context/AiRegistryContext";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AiRegistryProvider>
      <App />
    </AiRegistryProvider>
  </StrictMode>,
);
