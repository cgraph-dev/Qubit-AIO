import "./sidebar-frame.css";
export function SidebarFrame() {
  return <img className="qubit-sidebar__frame" src={new URL("./sidebar-frame.svg", import.meta.url).href} alt="" draggable={false} />;
}
