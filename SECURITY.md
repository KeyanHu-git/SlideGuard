# Security policy

## Supported version

Security fixes are applied to the latest tagged release.

## Report a problem

Do not open a public issue for a vulnerability that can expose local files or run code. Use GitHub's private vulnerability reporting for this repository.

SlideGuard treats PPTX and SVG input as untrusted. XML entity and network resolution are disabled, SVG scripts and external resources fail validation, PowerPoint opens presentations read-only with macros disabled, and no source file is uploaded by the program.

