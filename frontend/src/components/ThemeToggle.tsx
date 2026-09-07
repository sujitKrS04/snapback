import { motion } from "framer-motion";
import { useTheme } from "../context/ThemeContext";

interface ThemeToggleProps {
  className?: string;
  showLabel?: boolean;
}

export function ThemeToggle({ className = "", showLabel = false }: ThemeToggleProps) {
  const { isDark, toggleTheme } = useTheme();

  return (
    <button
      onClick={toggleTheme}
      className={`group relative inline-flex items-center gap-2 rounded-full border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-1.5 shadow-[var(--shadow-sm)] backdrop-blur-md transition-all duration-300 hover:border-[var(--border-strong)] hover:bg-[var(--bg-surface-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] cursor-pointer ${className}`}
      aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
      title={isDark ? "Switch to light theme" : "Switch to dark theme"}
    >
      <div className="relative flex h-6 w-12 items-center rounded-full bg-[var(--bg-base)] px-1 transition-colors">
        {/* Animated sliding thumb */}
        <motion.div
          className="flex h-5 w-5 items-center justify-center rounded-full bg-[var(--accent)] text-[var(--accent-fg)] shadow-sm"
          animate={{
            x: isDark ? 0 : 20,
            rotate: isDark ? 0 : 180,
          }}
          transition={{ type: "spring", stiffness: 500, damping: 30 }}
        >
          {isDark ? (
            /* Moon icon */
            <svg
              className="h-3 w-3"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />
            </svg>
          ) : (
            /* Sun icon */
            <svg
              className="h-3.2 w-3.2"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="4" />
              <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
            </svg>
          )}
        </motion.div>
      </div>

      {showLabel && (
        <span className="pr-2 text-xs font-medium text-[var(--text-secondary)] transition-colors group-hover:text-[var(--text-primary)]">
          {isDark ? "Dark" : "Light"}
        </span>
      )}
    </button>
  );
}
