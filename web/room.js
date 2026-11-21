export async function openRoom(stay) {
  const { mountRoom } = await import("./src/main.jsx");
  mountRoom(stay);
}
