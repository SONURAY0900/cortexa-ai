// Reveal sections as they scroll into view.
(function () {
    const sections = document.querySelectorAll(".reveal");
    if (!("IntersectionObserver" in window)) {
        sections.forEach((s) => s.classList.add("visible"));
        return;
    }
    const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
            if (entry.isIntersecting) {
                entry.target.classList.add("visible");
                observer.unobserve(entry.target);
            }
        });
    }, { threshold: 0.12 });
    sections.forEach((s) => observer.observe(s));
})();
