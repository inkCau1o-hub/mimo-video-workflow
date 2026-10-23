import React from "react";
import { createRoot } from "react-dom/client";
import { LauncherShell } from "./launcher/LauncherShell.jsx";
import "./styles.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <LauncherShell />
  </React.StrictMode>
);
