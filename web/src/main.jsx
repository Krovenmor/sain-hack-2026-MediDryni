import "./app.css";
import { createRoot } from "react-dom/client";
import { App } from "./App.jsx";

export function mountRoom(stay) {
  document.getElementById("entry-style")?.remove();
  document.title = "Ваш номер · Партнёрский отель сна";
  createRoot(document.getElementById("root")).render(<App stay={stay} />);
}
