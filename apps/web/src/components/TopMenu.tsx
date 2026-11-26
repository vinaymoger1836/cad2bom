import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router";
import { getConfig } from "../lib/api";
import { lastJobId } from "../lib/storage";
import { ThemeMenu } from "./ThemeMenu";

/** Top bar: skip link target is <main id="main">; current page is marked with aria-current. */
export function TopMenu() {
  // Re-read on navigation so "Upload files" appears right after a job is created.
  useLocation();
  const job = lastJobId();
  // The admin page is linked only when the server has it turned on.
  const [adminOn, setAdminOn] = useState(false);
  useEffect(() => {
    getConfig()
      .then((c) => {
        setAdminOn(c.admin_enabled === true);
      })
      .catch(() => {
        setAdminOn(false);
      });
  }, []);
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
            {adminOn ? (
              <li>
                <NavLink to="/admin" className="nav-link">
                  Library admin
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
