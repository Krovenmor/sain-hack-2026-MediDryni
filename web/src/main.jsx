import "./app.css";
import { createRoot } from "react-dom/client";
import { demoStay } from "./data/demoStay.js";
import { App } from "./App.jsx";

createRoot(document.getElementById("root")).render(<App stay={demoStay} />);
