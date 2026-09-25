package cmd

import (
	"plandex-cli/auth"
	"plandex-cli/term"

	"github.com/spf13/cobra"
)

var pin string
var localHost string
var validateOnly bool

var signInCmd = &cobra.Command{
	Use:   "sign-in",
	Short: "Sign in to a Plandex account",
	Args:  cobra.NoArgs,
	Run:   signIn,
}

func init() {
	RootCmd.AddCommand(signInCmd)

	signInCmd.Flags().StringVar(&pin, "pin", "", "Sign in with a pin from the Plandex Cloud web UI")
	signInCmd.Flags().StringVar(&localHost, "local-host", "", "Sign in non-interactively to a self-hosted local-mode server")
	signInCmd.Flags().BoolVar(&validateOnly, "validate-only", false, "Validate existing local auth without creating a session")
	signInCmd.MarkFlagsMutuallyExclusive("pin", "local-host")
}

func signIn(cmd *cobra.Command, args []string) {
	if validateOnly && localHost == "" {
		term.OutputErrorAndExit("Error validating local auth: --local-host is required")
	}
	if localHost != "" {
		var err error
		if validateOnly {
			err = auth.ValidateLocalAuth(localHost)
		} else {
			err = auth.SignInLocal(localHost)
		}
		if err != nil {
			term.OutputErrorAndExit("Error signing in to local mode: %v", err)
		}
		return
	}
	if pin != "" {
		err := auth.SignInWithCode(pin, "")

		if err != nil {
			term.OutputErrorAndExit("Error signing in: %v", err)
		}

		return
	}

	err := auth.SelectOrSignInOrCreate()

	if err != nil {
		term.OutputErrorAndExit("Error signing in: %v", err)
	}
}
