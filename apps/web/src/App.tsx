import { useEffect, useRef } from "react";
import { Link, Route, Routes, useLocation } from "react-router";
import { TopMenu } from "./components/TopMenu";
import { JobUpload } from "./screens/JobUpload";
import { MappingConfirm } from "./screens/MappingConfirm";
import { NewBom } from "./screens/NewBom";
import { ReviewScreen } from "./review/ReviewScreen";

/** On route change, move focus to the page heading so keyboard and screen-reader users land on the new screen. */
function FocusOnNavigate({ children }: { children: React.ReactNode }) {
  const { pathname } = useLocation();
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    ref.current?.focus({ preventScroll: false });
  }, [pathname]);
  return (
    <main
      id="main"
      ref={ref}
      tabIndex={-1}
      className={pathname.endsWith("/review") ? "container container-wide" : "container"}
    >
      {children}
    </main>
  );
}

function NotFound() {
  return (
    <div className="stack">
      <h1>Page not found</h1>
      <p>
        There is nothing at this address. <Link to="/">Start a new BoM</Link>.
      </p>
    </div>
  );
}

export function App() {
  return (
    <>
      <a href="#main" className="skip-link">
        Skip to main content
      </a>
      <TopMenu />
      <FocusOnNavigate>
        <Routes>
          <Route path="/" element={<NewBom />} />
          <Route path="/j/:id" element={<JobUpload />} />
          <Route path="/j/:id/mapping" element={<MappingConfirm />} />
          <Route path="/j/:id/review" element={<ReviewScreen />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </FocusOnNavigate>
      <footer className="footer">
        <p className="small muted">
          Generated BoM drafts must be reviewed before they are used for purchasing or production.
        </p>
      </footer>
    </>
  );
}
