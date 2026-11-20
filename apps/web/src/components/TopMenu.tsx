import { NavLink, useLocation } from "react-router";
import { lastJobId } from "../lib/storage";
import { ThemeMenu } from "./ThemeMenu";

/** Top bar: skip link target is <main id="main">; current page is marked with aria-current. */
export function TopMenu() {
  // Re-read on navigation so "Upload files" appears right after a job is created.
  useLocation();
  const job = lastJobId();
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <NavLink to="/" className="brand" aria-label="CAD-to-BoM home">
          <img src="/favicon.svg" alt="" width="28" height="28" />
          <span>CAD-to-BoM</span>
        </NavLink>
        <nav aria-label="Main" className="nav">
          <ul>
            <li>
              <NavLink to="/" end className="nav-link">
                New BoM
              </NavLink>
            </li>
            {job ? (
              <li>
                <NavLink to={`/j/${job}`} className="nav-link">
                  Upload files
                </NavLink>
              </li>
            ) : null}
          </ul>
        </nav>
        <ThemeMenu />
      </div>
    </header>
  );
}
