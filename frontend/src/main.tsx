import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { ModelProvider } from "./state/model";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ModelProvider>
      <App />
    </ModelProvider>
  </React.StrictMode>
);
