// Builds static/vendor/tailwind.css from the classes used in the UI.
// Rebuild after changing classes in index.html or app.js:
//   npx tailwindcss@3.4.17 -c tailwind/tailwind.config.js -i tailwind/input.css -o static/vendor/tailwind.css --minify
// (run from src/app). deploy_app.yml also rebuilds it on every deployment.
module.exports = {
  content: ['./static/index.html', './static/app.js'],
  theme: { extend: {} },
  plugins: [],
};
