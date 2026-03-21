/* This file keeps the shared app behavior for lightweight page interactions. */

(() => {
  const body = document.body;

  if (!body) {
    return;
  }

  body.classList.add("js-motion");

  const revealNodes = [...document.querySelectorAll("[data-reveal]")];

  if (revealNodes.length === 0) {
    return;
  }

  if (!("IntersectionObserver" in window)) {
    revealNodes.forEach((node) => node.classList.add("is-visible"));
    return;
  }

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      });
    },
    {
      threshold: 0.18,
      rootMargin: "0px 0px -8% 0px",
    },
  );

  revealNodes.forEach((node) => observer.observe(node));
})();
