#!/usr/bin/env node
/**
 * Optional MDX validator for the auggie-deep-wiki skill.
 *
 * Reads MDX content from stdin and attempts to compile it with
 * @mdx-js/mdx. Prints a JSON result and exits 0 on success, 1 on
 * validation errors, or 2 on fatal errors (including the package
 * not being installed).
 *
 * Setup (optional):
 *   npm install -g @mdx-js/mdx
 *   # or, in the skill directory:
 *   cd ~/.augment/skills/auggie-deep-wiki && npm init -y && npm install @mdx-js/mdx
 */

async function loadCompile() {
  try {
    const mod = await import("@mdx-js/mdx");
    return mod.compile;
  } catch (err) {
    console.error(
      JSON.stringify(
        {
          valid: false,
          errors: [
            {
              message:
                "@mdx-js/mdx is not installed. Run `npm install -g @mdx-js/mdx` to enable validation.",
            },
          ],
        },
        null,
        2,
      ),
    );
    process.exit(2);
  }
}

async function main() {
  const compile = await loadCompile();
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const content = Buffer.concat(chunks).toString("utf-8");

  try {
    await compile(content, { development: true });
    console.log(JSON.stringify({ valid: true, errors: [] }));
    process.exit(0);
  } catch (error) {
    const errors = [];
    if (error.position) {
      errors.push({
        message: error.reason || error.message,
        line: error.position.start?.line,
        column: error.position.start?.column,
      });
    } else {
      errors.push({ message: error.message });
    }
    console.log(JSON.stringify({ valid: false, errors }, null, 2));
    process.exit(1);
  }
}

main();
