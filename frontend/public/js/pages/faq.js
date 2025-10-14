document.addEventListener('DOMContentLoaded', function() {
  const faqQuestions = document.querySelectorAll('.faq-question');

  faqQuestions.forEach(question => {
    question.addEventListener('click', function() {
      const answer = this.nextElementSibling;
      const icon = this.querySelector('.faq-icon');

      // Toggle the current answer
      answer.classList.toggle('hidden');

      // Update the icon
      if (answer.classList.contains('hidden')) {
        icon.textContent = '+';
      } else {
        icon.textContent = '−';
      }
    });
  });
});
