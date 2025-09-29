/** Landing page interactions (formerly hero-scroll.js) */
document.addEventListener('DOMContentLoaded', () => {
  const heroSection = document.querySelector('.hero-gradient');
  const howItWorksSection = document.querySelector('section.bg-white');
  if (!heroSection || !howItWorksSection) return;
  let scrolled = false;
  heroSection.addEventListener(
    'wheel',
    (e) => {
      if (!scrolled && e.deltaY > 0) {
        scrolled = true;
        howItWorksSection.scrollIntoView({ behavior: 'smooth' });
        setTimeout(() => {
          scrolled = false;
        }, 1000);
        e.preventDefault();
      }
    },
    { passive: false }
  );
});
