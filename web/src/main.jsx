import { createRoot } from "react-dom/client";
import { demoStay } from "./demoStay.js";
import { Welcome } from "./Welcome.jsx";
import "../welcome.css";

createRoot(document.getElementById("root")).render(<Welcome stay={demoStay} />);
