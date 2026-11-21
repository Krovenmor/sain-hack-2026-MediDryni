import menuIcon from "../../assets/menu.svg";

function closeOnBackdrop(event) {
  const dialog = event.currentTarget;
  if (event.target !== dialog) return;
  const { left, right, top, bottom } = dialog.getBoundingClientRect();
  if (event.clientX < left || event.clientX > right || event.clientY < top || event.clientY > bottom) {
    dialog.close();
  }
}

export function Navigation({ dialogRef, screen, onNavigate }) {
  return (
    <dialog ref={dialogRef} className="drawer" id="menu" aria-labelledby="menu-title" onClick={closeOnBackdrop}>
      <button className="menu-button close-menu" aria-label="Закрыть навигацию" onClick={() => dialogRef.current.close()}>
        <img src={menuIcon} alt="" />
      </button>
      <h2 id="menu-title">Навигация</h2>
      <nav aria-label="Разделы">
        {[
          ["home", "Главная"],
          ["scenario", "Сценарий"],
          ["light", "Умная настройка"],
          ["manual", "Ручная настройка"],
        ].map(([id, name]) => (
          <button key={id} aria-current={screen === id ? "page" : undefined} onClick={() => onNavigate(id)}>
            {name}
          </button>
        ))}
      </nav>
    </dialog>
  );
}
